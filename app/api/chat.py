"""Endpoint de chat em JSON (SRS §3.7.3, RF-CHAT-01 a 12)."""

from __future__ import annotations

import time
from typing import cast

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from app.ai.base import (
    AIProvider,
    ChatMessage,
    ProviderError,
    provider_error_code,
)
from app.api.deps import CurrentUser, get_current_user
from app.config import Settings
from app.core.chat import context_errors
from app.core.chat_stream import StreamSession, error_code, stream_events
from app.core.errors import (
    ApiError,
    ValidationErrorItem,
    request_id_of,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.core.ip import get_client_ip
from app.core.logging import get_logger
from app.core.models import ModelCatalog, is_model_selectable
from app.core.sse import SSE_HEADERS
from app.core.stats import UsageStats
from app.core.usage import rate_limit_headers
from app.core.usage_store import UsageDecision, UsageStore
from app.schemas.chat import AssistantMessage, ChatRequest, ChatResponse
from app.schemas.usage import UsageInfo

router = APIRouter(prefix="/api/v1", tags=["Chat"])
logger = get_logger("nexa.chat")

CHAT_PATH = "/api/v1/chat"


def _build_context(payload: ChatRequest, settings: Settings) -> list[ChatMessage]:
    """Contexto enviado ao provedor: prompt padrão + mensagens do cliente."""
    context: list[ChatMessage] = []
    if settings.default_system_prompt:
        context.append(ChatMessage(role="system", content=settings.default_system_prompt))
    context.extend(
        ChatMessage(role=message.role, content=message.content) for message in payload.messages
    )
    return context


def _consume_or_raise(
    decision: UsageDecision,
) -> UsageInfo:
    """Converte uma recusa de cota no `429` correspondente."""
    if decision.allowed:
        return decision.usage

    code = decision.code or "rate_limited"
    headers = rate_limit_headers(decision.usage)
    headers["Retry-After"] = str(decision.retry_after)
    resets_at = decision.usage.resets_at if code.endswith("_daily_limit_reached") else None
    raise ApiError(code, headers=headers, resets_at=resets_at)


async def _quota_headers(store: UsageStore, *, user: CurrentUser, ip: str) -> dict[str, str]:
    """Cabeçalhos de cota do estado atual (vazio para admin)."""
    usage = await store.snapshot(role=user.role, subject=user.subject, ip=ip)
    return rate_limit_headers(usage)


async def _request_quota_headers(request: Request) -> dict[str, str]:
    """Cota do chamador a partir do token; vazio se não houver identidade."""
    try:
        user = get_current_user(request)
    except ApiError:
        return {}
    settings = cast(Settings, request.app.state.settings)
    store = cast(UsageStore, request.app.state.usage_store)
    ip = get_client_ip(request, settings.trusted_proxies)
    return await _quota_headers(store, user=user, ip=ip)


async def validation_error_with_quota(request: Request, exc: Exception) -> JSONResponse:
    """422 padrão; em `POST /chat`, acrescenta os cabeçalhos de RF-USO-08."""
    if not isinstance(exc, RequestValidationError):
        return await unhandled_exception_handler(request, exc)
    response = await validation_error_handler(request, exc)
    if request.method != "POST" or request.url.path.rstrip("/") != CHAT_PATH:
        return response
    for name, value in (await _request_quota_headers(request)).items():
        response.headers[name] = value
    return response


def _provider_error(exc: ProviderError) -> ApiError:
    return ApiError(error_code(exc))


async def _resolve_model(
    payload: ChatRequest,
    request: Request,
    *,
    user: CurrentUser,
    store: UsageStore,
    ip: str,
) -> str:
    """Modelo efetivo: o escolhido (validado) ou o padrão do servidor."""
    provider = cast(AIProvider, request.app.state.ai_provider)
    if payload.model is None:
        return provider.model

    settings = cast(Settings, request.app.state.settings)
    catalog = cast(ModelCatalog, request.app.state.model_catalog)
    headers = await _quota_headers(store, user=user, ip=ip)

    try:
        resolved = await catalog.resolve(
            payload.model,
            request_id=request_id_of(request),
        )
    except ProviderError as exc:
        # Consultar o catálogo não consome cota; o erro real é de disponibilidade.
        raise ApiError(provider_error_code(exc), headers=headers) from exc

    if resolved is None:
        raise ApiError(
            "validation_error",
            errors=[
                ValidationErrorItem(
                    field="model",
                    reason="modelo não instalado no Ollama",
                )
            ],
            headers=headers,
        )

    if not is_model_selectable(
        resolved,
        allowed=settings.ai_allowed_models,
        default_model=provider.model,
    ):
        raise ApiError(
            "validation_error",
            errors=[
                ValidationErrorItem(
                    field="model",
                    reason="modelo não permitido pelo servidor",
                )
            ],
            headers=headers,
        )
    return resolved


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Envia mensagens ao provedor de IA",
    description=(
        "Recebe o contexto completo e devolve a resposta do modelo. "
        "Com `stream = false` a resposta é um JSON completo; com `stream = true` "
        "(padrão) a resposta é um fluxo SSE com `data:` por trecho, evento "
        "`done` no fim e evento `error` em falhas após o início (3.7.3)."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": "Resposta JSON (`stream = false`) ou fluxo SSE (`stream = true`).",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        'data: {"content":"SSE é "}\n\n'
                        "event: done\n"
                        'data: {"status":"complete","finish_reason":"stop",'
                        '"usage":{"daily_limit":10,"daily_remaining":6,'
                        '"per_minute_limit":3,"per_minute_remaining":2,'
                        '"resets_at":"2026-10-29T00:00:00Z"}}\n\n'
                    ),
                }
            },
        },
        status.HTTP_401_UNAUTHORIZED: {"description": "Token ausente ou inválido."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Contexto inválido ou acima dos limites do papel."
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Cota ou limite por minuto excedido."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Erro do provedor de IA."},
        status.HTTP_504_GATEWAY_TIMEOUT: {"description": "Timeout do provedor de IA."},
    },
)
async def chat(
    payload: ChatRequest,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
) -> ChatResponse | StreamingResponse:
    settings = cast(Settings, request.app.state.settings)
    ip = get_client_ip(request, settings.trusted_proxies)
    store = cast(UsageStore, request.app.state.usage_store)
    stats = cast(UsageStats, request.app.state.stats)

    # RF-ADM-02: toda requisição de chat autenticada entra nas estatísticas.
    stats.record_chat(subject=user.subject, role=user.role)

    errors = context_errors(payload.messages, role=user.role, settings=settings)
    if errors:
        # RF-USO-08: toda resposta de guest traz os cabeçalhos de cota.
        headers = await _quota_headers(store, user=user, ip=ip)
        raise ApiError("validation_error", errors=errors, headers=headers)

    # Validar o modelo antes de consumir cota: escolha inválida não debita uso.
    selected_model = await _resolve_model(payload, request, user=user, store=store, ip=ip)

    decision = await store.consume(role=user.role, subject=user.subject, ip=ip)
    if not decision.allowed:
        stats.record_rate_limited()
        # RNF-OBS-04: limite atingido é evento de segurança auditável em log.
        logger.warning(
            "chat_rate_limited",
            role=user.role,
            code=decision.code,
            retry_after=decision.retry_after,
        )
    usage = _consume_or_raise(decision)

    provider = cast(AIProvider, request.app.state.ai_provider)
    context = _build_context(payload, settings)

    if payload.stream:
        # RF-CHAT-03/07: o consumo já ocorreu; o fluxo controla refund e falhas.
        session = StreamSession(
            provider=provider,
            messages=context,
            usage=usage,
            store=store,
            role=user.role,
            subject=user.subject,
            ip=ip,
            max_tokens=settings.ai_max_output_tokens,
            max_seconds=settings.ai_max_output_seconds,
            request_id=request_id_of(request),
            idle_timeout_seconds=settings.ai_stream_idle_timeout_seconds,
            stats=stats,
            model=selected_model,
        )
        return StreamingResponse(
            stream_events(session),
            headers={**rate_limit_headers(usage), **SSE_HEADERS},
        )

    started = time.perf_counter()

    try:
        result = await provider.generate(
            context,
            max_tokens=settings.ai_max_output_tokens,
            max_seconds=settings.ai_max_output_seconds,
            request_id=request_id_of(request),
            model=selected_model,
        )
    except Exception as exc:
        # RF-USO-05/CA-13: nada foi entregue ao cliente, devolve a cota.
        await store.refund(role=user.role, subject=user.subject, ip=ip)
        if isinstance(exc, ProviderError):
            stats.record_provider_error()
            stats.record_duration(time.perf_counter() - started)
            api_error = _provider_error(exc)
            # Após o refund, a cota reportada é a efetivamente disponível.
            api_error.headers = await _quota_headers(store, user=user, ip=ip)
            logger.warning(
                "chat_provider_failed",
                role=user.role,
                provider=provider.name,
                model=selected_model,
                code=api_error.code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise api_error from exc
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    stats.record_duration(duration_ms / 1000)
    logger.info(
        "chat_completed",
        role=user.role,
        provider=provider.name,
        model=selected_model,
        finish_reason=result.finish_reason,
        duration_ms=duration_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )

    for name, value in rate_limit_headers(usage).items():
        response.headers[name] = value

    return ChatResponse(
        message=AssistantMessage(content=result.content),
        provider=provider.name,
        model=selected_model,
        finish_reason=result.finish_reason,
        usage=usage,
    )


__all__ = ["router", "validation_error_with_quota"]

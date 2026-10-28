"""Endpoint de chat em JSON (SRS §3.7.3, RF-CHAT-01 a 12)."""

from __future__ import annotations

import time
from typing import cast

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.ai.base import (
    AIProvider,
    ChatMessage,
    ProviderError,
    ProviderTimeout,
)
from app.api.deps import CurrentUser, get_current_user
from app.config import Settings
from app.core.chat import context_errors
from app.core.errors import (
    ApiError,
    request_id_of,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.core.ip import get_client_ip
from app.core.logging import get_logger
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
    if isinstance(exc, ProviderTimeout):
        return ApiError("ai_provider_timeout")
    return ApiError("ai_provider_error")


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Envia mensagens ao provedor de IA",
    description=(
        "Recebe o contexto completo e devolve a resposta do modelo. "
        "`stream = false` retorna JSON; o modo SSE está descrito em 3.7.3."
    ),
    responses={
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
) -> ChatResponse:
    settings = cast(Settings, request.app.state.settings)
    ip = get_client_ip(request, settings.trusted_proxies)
    store = cast(UsageStore, request.app.state.usage_store)

    if payload.stream:
        # O modo SSE é implementado na fase de streaming; por ora, só JSON.
        headers = await _quota_headers(store, user=user, ip=ip)
        raise ApiError(
            "bad_request",
            detail="O modo streaming não está disponível nesta versão da API.",
            headers=headers,
        )

    errors = context_errors(payload.messages, role=user.role, settings=settings)
    if errors:
        # RF-USO-08: toda resposta de guest traz os cabeçalhos de cota.
        headers = await _quota_headers(store, user=user, ip=ip)
        raise ApiError("validation_error", errors=errors, headers=headers)

    decision = await store.consume(role=user.role, subject=user.subject, ip=ip)
    usage = _consume_or_raise(decision)

    provider = cast(AIProvider, request.app.state.ai_provider)
    context = _build_context(payload, settings)
    started = time.perf_counter()

    try:
        result = await provider.generate(
            context,
            max_tokens=settings.ai_max_output_tokens,
            max_seconds=settings.ai_max_output_seconds,
            request_id=request_id_of(request),
        )
    except Exception as exc:
        # RF-USO-05/CA-13: nada foi entregue ao cliente, devolve a cota.
        await store.refund(role=user.role, subject=user.subject, ip=ip)
        if isinstance(exc, ProviderError):
            api_error = _provider_error(exc)
            # Após o refund, a cota reportada é a efetivamente disponível.
            api_error.headers = await _quota_headers(store, user=user, ip=ip)
            logger.warning(
                "chat_provider_failed",
                role=user.role,
                provider=provider.name,
                code=api_error.code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise api_error from exc
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "chat_completed",
        role=user.role,
        provider=provider.name,
        model=provider.model,
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
        model=provider.model,
        finish_reason=result.finish_reason,
        usage=usage,
    )


__all__ = ["router", "validation_error_with_quota"]

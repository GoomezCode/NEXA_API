"""Compatibilidade Ollama: `/api/chat`, `/api/tags`, `/api/show` e `/api/ps` (F16)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse

from app.ai.base import (
    ChatMessage,
    GatewayError,
    ModelInfo,
    ModelNotAllowed,
    ModelNotFound,
    ProviderError,
    ToolCall,
    ToolSpec,
    UnsupportedCapability,
    provider_error_code,
)
from app.ai.capabilities import merge_capabilities
from app.ai.router import (
    CatalogSnapshot,
    ProviderCatalogEntry,
    ProviderRouter,
    ResolvedModel,
    canonical_model_id,
)
from app.compat.auth import (
    CompatIdentity,
    CompatLimits,
    enforce_compat_limits,
    get_compat_identity,
    refund_compat,
)
from app.compat.errors import CompatApiError
from app.compat.ollama_stream import (
    NDJSON_HEADERS,
    OllamaStreamContext,
    ollama_done_reason,
    ollama_stream,
    ollama_tool_calls,
)
from app.config import Settings
from app.core.api_keys import SCOPE_CHAT, SCOPE_MODELS
from app.core.chat import context_errors
from app.core.clock import Clock
from app.core.errors import request_id_of
from app.core.ip import get_client_ip
from app.core.logging import get_logger
from app.core.stats import UsageStats
from app.schemas.compat_ollama import (
    OllamaChatMessage,
    OllamaChatRequest,
    OllamaChatResponse,
    OllamaModelDetails,
    OllamaPsResponse,
    OllamaShowRequest,
    OllamaShowResponse,
    OllamaTagModel,
    OllamaTagsResponse,
)

router = APIRouter(prefix="/api", tags=["Compatibilidade Ollama"])
logger = get_logger("nexa.compat.ollama")

_SECURITY: dict[str, Any] = {"security": [{"HTTPBearer": []}]}


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)


def _iso(value: datetime | None, fallback: datetime) -> str:
    moment = value or fallback
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(model_id: str) -> str:
    return f"sha256:{hashlib.sha256(model_id.encode('utf-8')).hexdigest()}"


def _details(entry: ProviderCatalogEntry, model: ModelInfo) -> OllamaModelDetails:
    return OllamaModelDetails(
        family=model.family or entry.provider,
        parameter_size=model.parameter_size or "",
        quantization_level=model.quantization_level or "",
    )


def _gateway_error(exc: GatewayError, *, requested: str) -> CompatApiError:
    if isinstance(exc, ModelNotFound):
        return CompatApiError("model_not_found", message=f"model '{requested}' not found")
    if isinstance(exc, ModelNotAllowed):
        return CompatApiError("model_not_allowed", message=f"model '{requested}' not allowed")
    if isinstance(exc, UnsupportedCapability):
        return CompatApiError("unsupported_capability")
    return CompatApiError(exc.code, message=str(exc))


def _to_internal(
    payload: OllamaChatRequest,
    settings: Settings,
) -> tuple[list[ChatMessage], list[ToolSpec] | None, bool]:
    context: list[ChatMessage] = []
    if settings.default_system_prompt:
        context.append(ChatMessage(role="system", content=settings.default_system_prompt))

    has_images = False
    for message in payload.messages:
        content = message.content
        if message.images:
            has_images = True
            content = f"{content}\n[imagem]".strip()
        tool_calls = tuple(
            ToolCall(
                id=f"call_{index}",
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for index, call in enumerate(message.tool_calls or ())
        )
        context.append(
            ChatMessage(
                role=message.role,
                content=content,
                tool_calls=tool_calls,
                tool_call_id=message.tool_name,
            )
        )

    tools: list[ToolSpec] | None = None
    if payload.tools:
        tools = [
            ToolSpec(
                name=tool.function.name,
                description=tool.function.description,
                parameters=tool.function.parameters,
            )
            for tool in payload.tools
        ]
    return context, tools, has_images


async def _resolve(
    request: Request,
    payload: OllamaChatRequest,
    *,
    tools: Sequence[ToolSpec] | None,
    has_images: bool,
) -> ResolvedModel:
    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    try:
        return await ai_router.resolve(
            payload.model,
            stream=payload.stream,
            tools=tools,
            has_images=has_images,
            json_mode=payload.wants_json(),
            request_id=request_id_of(request),
        )
    except GatewayError as exc:
        raise _gateway_error(exc, requested=payload.model) from exc
    except ProviderError as exc:
        raise CompatApiError(provider_error_code(exc)) from exc


def _validation_error(
    context: Sequence[ChatMessage],
    identity: CompatIdentity,
    settings: Settings,
) -> None:
    errors = context_errors(
        context,
        role=identity.role,
        settings=settings,
        allow_system=identity.role != "guest",
    )
    if errors:
        raise CompatApiError(
            "validation_error",
            message=errors[0].reason,
            param=errors[0].field,
        )


def _refund_callable(
    request: Request,
    identity: CompatIdentity,
    *,
    ip: str,
    limits: CompatLimits,
) -> Callable[[], Awaitable[None]] | None:
    if not limits.refundable:
        return None

    async def refund() -> None:
        await refund_compat(request, identity, ip=ip)

    return refund


async def _rate_limit_for_key(
    request: Request,
    identity: CompatIdentity,
    *,
    ip: str,
    response: Response,
) -> CompatLimits | None:
    """Rotas de catálogo não debitam cota; a chave ainda respeita o limite/min."""
    if identity.kind != "api_key":
        return None
    limits = await enforce_compat_limits(request, identity, ip=ip)
    for name, value in limits.headers.items():
        response.headers[name] = value
    return limits


async def _catalog(request: Request) -> CatalogSnapshot:
    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    try:
        return await ai_router.list_catalog(request_id=request_id_of(request))
    except ProviderError as exc:
        raise CompatApiError(provider_error_code(exc)) from exc


def _models(
    snapshot: CatalogSnapshot,
) -> Iterator[tuple[ProviderCatalogEntry, ModelInfo]]:
    for entry in snapshot.providers:
        for model in entry.models:
            yield entry, model


def _tags(
    snapshot: CatalogSnapshot,
    fallback: datetime,
) -> list[OllamaTagModel]:
    tags: list[OllamaTagModel] = []
    for entry, model in _models(snapshot):
        model_id = canonical_model_id(entry.provider, model.name)
        tags.append(
            OllamaTagModel(
                name=model_id,
                model=model_id,
                modified_at=_iso(model.modified_at, fallback),
                size=model.size_bytes or 0,
                digest=_digest(model_id),
                details=_details(entry, model),
            )
        )
    return tags


def _find_model(
    snapshot: CatalogSnapshot,
    requested: str,
) -> tuple[ProviderCatalogEntry, ModelInfo] | None:
    wanted = {requested, requested.removesuffix(":latest")}
    for entry, model in _models(snapshot):
        model_id = canonical_model_id(entry.provider, model.name)
        candidates = {
            model.name,
            model.name.removesuffix(":latest"),
            model_id,
            model_id.removesuffix(":latest"),
        }
        if wanted & candidates:
            return entry, model
    return None


def _capabilities(entry: ProviderCatalogEntry, model: ModelInfo) -> list[str]:
    capabilities = merge_capabilities(entry.capabilities, model)
    names: list[str] = []
    if capabilities.chat:
        names.append("completion")
    if capabilities.tools:
        names.append("tools")
    if capabilities.vision:
        names.append("vision")
    return names


@router.post(
    "/chat",
    response_model=OllamaChatResponse,
    summary="Chat no formato Ollama",
    description=(
        "Traduz `model`, `messages`, `tools`, `options` e `format` para o pipeline "
        "nativo. Devolve um objeto JSON ou NDJSON com `done:false` por trecho e uma "
        "linha final `done:true` (streaming é o padrão do Ollama)."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": "Mensagem JSON ou stream NDJSON.",
            "content": {
                "application/x-ndjson": {
                    "schema": {"type": "string"},
                    "example": (
                        '{"model":"llama3.1:latest","created_at":"2026-10-29T14:30:00Z",'
                        '"message":{"role":"assistant","content":"Olá"},"done":false}\n'
                        '{"model":"llama3.1:latest","created_at":"2026-10-29T14:30:00Z",'
                        '"message":{"role":"assistant","content":"!"},"done":false}\n'
                        '{"model":"llama3.1:latest","created_at":"2026-10-29T14:30:00Z",'
                        '"message":{"role":"assistant","content":""},"done":true,'
                        '"done_reason":"stop","prompt_eval_count":12,"eval_count":8}\n'
                    ),
                }
            },
        },
        status.HTTP_400_BAD_REQUEST: {"description": "Requisição inválida."},
        status.HTTP_401_UNAUTHORIZED: {"description": "API key ausente ou inválida."},
        status.HTTP_404_NOT_FOUND: {"description": "Modelo inexistente."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit ou cota excedida."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Erro do provedor de IA."},
        status.HTTP_504_GATEWAY_TIMEOUT: {"description": "Timeout do provedor de IA."},
    },
    openapi_extra=_SECURITY,
)
async def chat(
    payload: OllamaChatRequest,
    request: Request,
    response: Response,
) -> OllamaChatResponse | StreamingResponse:
    settings = _settings(request)
    identity = await get_compat_identity(request, scope=SCOPE_CHAT, allow_localhost=True)
    ip = get_client_ip(request, settings.trusted_proxies)
    stats = cast(UsageStats, request.app.state.stats)
    stats.record_chat(subject=identity.subject, role=identity.role)

    context, tools, has_images = _to_internal(payload, settings)
    _validation_error(context, identity, settings)
    resolution = await _resolve(request, payload, tools=tools, has_images=has_images)
    limits = await enforce_compat_limits(request, identity, ip=ip, stats=stats)
    max_tokens = payload.requested_max_tokens(settings.ai_max_output_tokens)
    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    clock = _clock(request)
    created_at = _iso(clock.now(), clock.now())

    if payload.stream:
        stream_context = OllamaStreamContext(
            model=resolution.model,
            created_at=created_at,
            router=ai_router,
            resolution=resolution,
            messages=context,
            max_tokens=max_tokens,
            max_seconds=settings.ai_max_output_seconds,
            request_id=request_id_of(request),
            tools=tools,
            idle_timeout_seconds=settings.ai_stream_idle_timeout_seconds,
            refund=_refund_callable(request, identity, ip=ip, limits=limits),
        )
        return StreamingResponse(
            ollama_stream(stream_context),
            headers={**limits.headers, **NDJSON_HEADERS},
        )

    started = time.perf_counter()
    try:
        routed = await ai_router.generate(
            resolution,
            context,
            max_tokens=max_tokens,
            max_seconds=settings.ai_max_output_seconds,
            request_id=request_id_of(request),
            tools=tools,
        )
    except ProviderError as exc:
        refund = _refund_callable(request, identity, ip=ip, limits=limits)
        if refund is not None:
            await refund()
        stats.record_provider_error()
        stats.record_duration(time.perf_counter() - started)
        raise CompatApiError(provider_error_code(exc), headers=limits.headers) from exc

    result = routed.result
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    stats.record_duration(duration_ms / 1000)
    logger.info(
        "compat_ollama_completed",
        role=identity.role,
        key_prefix=identity.prefix,
        key_fingerprint=identity.fingerprint,
        provider=routed.resolution.provider_name,
        model=routed.resolution.model,
        finish_reason=result.finish_reason,
        duration_ms=duration_ms,
    )

    for name, value in limits.headers.items():
        response.headers[name] = value

    return OllamaChatResponse(
        model=routed.resolution.model,
        created_at=created_at,
        message=OllamaChatMessage(
            content=result.content,
            tool_calls=ollama_tool_calls(result.tool_calls),
        ),
        done_reason=ollama_done_reason(result.finish_reason),
        prompt_eval_count=result.prompt_tokens or 0,
        eval_count=result.completion_tokens or 0,
    )


@router.get(
    "/tags",
    response_model=OllamaTagsResponse,
    summary="Modelos no formato Ollama",
    description=(
        "Agrega o catálogo dos provedores habilitados. Os nomes são Model IDs "
        "canônicos (`provider/model`), aceitos de volta em `/api/chat` e "
        "`/api/show`. Consultar o catálogo não consome cota."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Lista de modelos instalados."},
        status.HTTP_401_UNAUTHORIZED: {"description": "API key ausente ou inválida."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit da API key excedido."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Resposta inválida do provedor."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Provedores indisponíveis."},
    },
    openapi_extra=_SECURITY,
)
async def tags(request: Request, response: Response) -> OllamaTagsResponse:
    settings = _settings(request)
    identity = await get_compat_identity(request, scope=SCOPE_MODELS, allow_localhost=True)
    ip = get_client_ip(request, settings.trusted_proxies)
    await _rate_limit_for_key(request, identity, ip=ip, response=response)
    snapshot = await _catalog(request)
    return OllamaTagsResponse(models=_tags(snapshot, _clock(request).now()))


@router.post(
    "/show",
    response_model=OllamaShowResponse,
    summary="Detalhes de um modelo no formato Ollama",
    description=(
        "Devolve `details`, `capabilities` e `modified_at` de um Model ID canônico "
        "ou do nome do modelo. Consultar o catálogo não consome cota."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Detalhes do modelo."},
        status.HTTP_400_BAD_REQUEST: {"description": "Requisição inválida."},
        status.HTTP_401_UNAUTHORIZED: {"description": "API key ausente ou inválida."},
        status.HTTP_404_NOT_FOUND: {"description": "Modelo inexistente."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit da API key excedido."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Resposta inválida do provedor."},
    },
    openapi_extra=_SECURITY,
)
async def show(
    payload: OllamaShowRequest,
    request: Request,
    response: Response,
) -> OllamaShowResponse:
    settings = _settings(request)
    identity = await get_compat_identity(request, scope=SCOPE_MODELS, allow_localhost=True)
    ip = get_client_ip(request, settings.trusted_proxies)
    await _rate_limit_for_key(request, identity, ip=ip, response=response)
    snapshot = await _catalog(request)
    found = _find_model(snapshot, payload.model)
    if found is None:
        raise CompatApiError("model_not_found", message=f"model '{payload.model}' not found")
    entry, model = found
    return OllamaShowResponse(
        model=canonical_model_id(entry.provider, model.name),
        details=_details(entry, model),
        capabilities=_capabilities(entry, model),
        modified_at=_iso(model.modified_at, _clock(request).now()),
    )


@router.get(
    "/ps",
    response_model=OllamaPsResponse,
    summary="Modelos em execução no formato Ollama",
    description=(
        "O gateway é stateless e não mantém modelos carregados, portanto responde "
        'sempre com `{"models":[]}`. A rota existe para clientes Ollama que a '
        "consultam na inicialização."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Lista vazia de modelos em execução."},
        status.HTTP_401_UNAUTHORIZED: {"description": "API key ausente ou inválida."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit da API key excedido."},
    },
    openapi_extra=_SECURITY,
)
async def ps(request: Request, response: Response) -> OllamaPsResponse:
    settings = _settings(request)
    identity = await get_compat_identity(request, scope=SCOPE_MODELS, allow_localhost=True)
    ip = get_client_ip(request, settings.trusted_proxies)
    await _rate_limit_for_key(request, identity, ip=ip, response=response)
    return OllamaPsResponse(models=[])


__all__ = ["router"]

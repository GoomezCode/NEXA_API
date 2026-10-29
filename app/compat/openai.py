"""Compatibilidade OpenAI: `/v1/chat/completions` e `/v1/models` (F14)."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse

from app.ai.base import (
    ChatMessage,
    GatewayError,
    ModelNotAllowed,
    ModelNotFound,
    ProviderError,
    ToolCall,
    ToolSpec,
    UnsupportedCapability,
    provider_error_code,
)
from app.ai.router import ProviderRouter, ResolvedModel, canonical_model_id
from app.compat.auth import (
    CompatIdentity,
    CompatLimits,
    enforce_compat_limits,
    get_compat_identity,
    refund_compat,
)
from app.compat.errors import CompatApiError
from app.compat.openai_stream import OpenAIStreamContext, openai_finish_reason, openai_stream
from app.config import Settings
from app.core.api_keys import SCOPE_CHAT, SCOPE_MODELS
from app.core.chat import context_errors
from app.core.clock import Clock
from app.core.errors import request_id_of
from app.core.ip import get_client_ip
from app.core.logging import get_logger
from app.core.sse import SSE_HEADERS
from app.core.stats import UsageStats
from app.schemas.compat_openai import (
    OpenAIAssistantMessage,
    OpenAIChatRequest,
    OpenAIChatResponse,
    OpenAIChoice,
    OpenAIModel,
    OpenAIModelList,
    OpenAIToolCallFunction,
    OpenAIToolCallOut,
    OpenAIUsage,
    dump_arguments,
)

router = APIRouter(prefix="/v1", tags=["Compatibilidade OpenAI"])
logger = get_logger("nexa.compat.openai")

_SECURITY: dict[str, Any] = {"security": [{"HTTPBearer": []}]}


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)


def _completion_id(request: Request) -> str:
    request_id = request_id_of(request).replace("-", "")
    return f"chatcmpl-nexa-{request_id[:24] or uuid4().hex[:24]}"


def _gateway_error(exc: GatewayError) -> CompatApiError:
    if isinstance(exc, ModelNotFound):
        return CompatApiError("model_not_found")
    if isinstance(exc, ModelNotAllowed):
        return CompatApiError("model_not_allowed")
    if isinstance(exc, UnsupportedCapability):
        param = "tools" if exc.capability == "tools" else "model"
        return CompatApiError("unsupported_capability", param=param)
    return CompatApiError(exc.code)


def _reject_unsupported(payload: OpenAIChatRequest) -> None:
    """Parâmetros documentados como não suportados retornam 400 (docs §3.3)."""
    if payload.n != 1:
        raise CompatApiError(
            "unsupported_parameter",
            param="n",
            message="Somente n = 1 é suportado.",
        )
    if payload.logprobs:
        raise CompatApiError(
            "unsupported_parameter",
            param="logprobs",
            message="logprobs não é suportado.",
        )
    if payload.top_logprobs:
        raise CompatApiError(
            "unsupported_parameter",
            param="top_logprobs",
            message="top_logprobs não é suportado.",
        )


def _tool_choice(value: str | dict[str, Any] | None) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        function = value.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                return name
        name = value.get("name")
        if isinstance(name, str) and name:
            return name
    return None


def _parse_arguments(raw: str, *, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError as exc:
        raise CompatApiError(
            "invalid_value",
            param=field,
            message="function.arguments deve ser um objeto JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise CompatApiError(
            "invalid_value",
            param=field,
            message="function.arguments deve ser um objeto JSON.",
        )
    return parsed


def _to_internal(
    payload: OpenAIChatRequest,
    settings: Settings,
) -> tuple[list[ChatMessage], list[ToolSpec] | None, str | None]:
    context: list[ChatMessage] = []
    if settings.default_system_prompt:
        context.append(ChatMessage(role="system", content=settings.default_system_prompt))

    for index, message in enumerate(payload.messages):
        tool_calls: tuple[ToolCall, ...] = ()
        if message.tool_calls:
            tool_calls = tuple(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=_parse_arguments(
                        call.function.arguments,
                        field=f"messages[{index}].tool_calls.function.arguments",
                    ),
                )
                for call in message.tool_calls
            )
        context.append(
            ChatMessage(
                role=message.role,
                content=message.content or "",
                tool_calls=tool_calls,
                tool_call_id=message.tool_call_id,
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
    return context, tools, _tool_choice(payload.tool_choice)


async def _resolve(
    request: Request,
    payload: OpenAIChatRequest,
    tools: Sequence[ToolSpec] | None,
) -> ResolvedModel:
    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    try:
        return await ai_router.resolve(
            payload.model,
            stream=payload.stream,
            tools=tools,
            json_mode=payload.wants_json(),
            request_id=request_id_of(request),
        )
    except GatewayError as exc:
        raise _gateway_error(exc) from exc
    except ProviderError as exc:
        raise CompatApiError(provider_error_code(exc)) from exc


def _usage(prompt_tokens: int | None, completion_tokens: int | None) -> OpenAIUsage:
    prompt = prompt_tokens or 0
    completion = completion_tokens or 0
    return OpenAIUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


@router.post(
    "/chat/completions",
    response_model=OpenAIChatResponse,
    summary="Chat no formato OpenAI",
    description=(
        "Traduz o contrato OpenAI para o pipeline nativo: valida modelo/capacidades, "
        "aplica cotas e rate limit da API key e devolve `chat.completion` (JSON) ou "
        "`chat.completion.chunk` seguido de `data: [DONE]` (streaming)."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": "Resposta JSON ou stream SSE.",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        'data: {"id":"chatcmpl-nexa-0192","object":"chat.completion.chunk",'
                        '"choices":[{"index":0,"delta":{"content":"Olá"},"finish_reason":null}]}\n\n'
                        'data: {"id":"chatcmpl-nexa-0192","object":"chat.completion.chunk",'
                        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                        "data: [DONE]\n\n"
                    ),
                }
            },
        },
        status.HTTP_400_BAD_REQUEST: {"description": "Parâmetro não suportado."},
        status.HTTP_401_UNAUTHORIZED: {"description": "API key ausente ou inválida."},
        status.HTTP_404_NOT_FOUND: {"description": "Modelo inexistente."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Requisição inválida."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit ou cota excedida."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Erro do provedor de IA."},
        status.HTTP_504_GATEWAY_TIMEOUT: {"description": "Timeout do provedor de IA."},
    },
    openapi_extra=_SECURITY,
)
async def create_chat_completion(
    payload: OpenAIChatRequest,
    request: Request,
    response: Response,
) -> OpenAIChatResponse | StreamingResponse:
    settings = _settings(request)
    identity = await get_compat_identity(request, scope=SCOPE_CHAT)
    ip = get_client_ip(request, settings.trusted_proxies)
    stats = cast(UsageStats, request.app.state.stats)
    stats.record_chat(subject=identity.subject, role=identity.role)

    _reject_unsupported(payload)
    context, tools, tool_choice = _to_internal(payload, settings)

    errors = context_errors(context, role=identity.role, settings=settings)
    if errors:
        raise CompatApiError(
            "validation_error",
            message=errors[0].reason,
            param=errors[0].field,
        )

    resolution = await _resolve(request, payload, tools)
    limits = await enforce_compat_limits(request, identity, ip=ip, stats=stats)
    max_tokens = payload.requested_max_tokens(settings.ai_max_output_tokens)
    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    completion_id = _completion_id(request)
    created = int(_clock(request).now().timestamp())

    if payload.stream:
        stream_context = OpenAIStreamContext(
            completion_id=completion_id,
            created=created,
            model=resolution.model,
            router=ai_router,
            resolution=resolution,
            messages=context,
            max_tokens=max_tokens,
            max_seconds=settings.ai_max_output_seconds,
            request_id=request_id_of(request),
            include_usage=bool(
                payload.stream_options is not None and payload.stream_options.include_usage
            ),
            idle_timeout_seconds=settings.ai_stream_idle_timeout_seconds,
            refund=_refund_callable(request, identity, ip=ip, limits=limits),
        )
        return StreamingResponse(
            openai_stream(stream_context),
            headers={**limits.headers, **SSE_HEADERS},
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
            tool_choice=tool_choice,
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
        "compat_openai_completed",
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

    tool_calls = [
        OpenAIToolCallOut(
            id=call.id,
            function=OpenAIToolCallFunction(
                name=call.name,
                arguments=dump_arguments(dict(call.arguments)),
            ),
        )
        for call in result.tool_calls
    ]
    return OpenAIChatResponse(
        id=completion_id,
        created=created,
        model=routed.resolution.model,
        choices=[
            OpenAIChoice(
                message=OpenAIAssistantMessage(
                    content=result.content or None,
                    tool_calls=tool_calls,
                ),
                finish_reason=openai_finish_reason(result.finish_reason),
            )
        ],
        usage=_usage(result.prompt_tokens, result.completion_tokens),
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


@router.get(
    "/models",
    response_model=OpenAIModelList,
    summary="Modelos no formato OpenAI",
    description=(
        "Lista o catálogo agregado dos provedores habilitados como "
        '`{"object":"list","data":[...]}`. Consultar o catálogo não consome cota.'
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "API key ausente ou inválida."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit da API key excedido."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Resposta inválida do provedor."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Provedores indisponíveis."},
    },
    openapi_extra=_SECURITY,
)
async def list_models(request: Request, response: Response) -> OpenAIModelList:
    settings = _settings(request)
    identity = await get_compat_identity(request, scope=SCOPE_MODELS)
    ip = get_client_ip(request, settings.trusted_proxies)
    if identity.kind == "api_key":
        limits = await enforce_compat_limits(request, identity, ip=ip)
        for name, value in limits.headers.items():
            response.headers[name] = value

    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    try:
        snapshot = await ai_router.list_catalog(
            request_id=request_id_of(request),
        )
    except ProviderError as exc:
        raise CompatApiError(provider_error_code(exc)) from exc

    created = int(_clock(request).now().timestamp())
    data: list[OpenAIModel] = []
    seen: set[str] = set()
    for provider in snapshot.providers:
        for model in provider.models:
            model_id = canonical_model_id(provider.provider, model.name)
            if model_id in seen:
                continue
            seen.add(model_id)
            data.append(OpenAIModel(id=model_id, created=created))
    return OpenAIModelList(data=data)


__all__ = ["router"]

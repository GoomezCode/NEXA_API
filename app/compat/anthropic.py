"""Compatibilidade Anthropic: `/v1/messages` e `count_tokens` (F15)."""

from __future__ import annotations

import math
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
from app.ai.router import ProviderRouter, ResolvedModel
from app.compat.anthropic_stream import (
    AnthropicStreamContext,
    anthropic_stop_reason,
    anthropic_stream,
)
from app.compat.auth import (
    CompatIdentity,
    CompatLimits,
    enforce_compat_limits,
    get_compat_identity,
    refund_compat,
)
from app.compat.errors import CompatApiError
from app.config import Settings
from app.core.api_keys import SCOPE_CHAT
from app.core.chat import context_errors
from app.core.errors import request_id_of
from app.core.ip import get_client_ip
from app.core.logging import get_logger
from app.core.sse import SSE_HEADERS
from app.core.stats import UsageStats
from app.schemas.compat_anthropic import (
    AnthropicContentBlock,
    AnthropicCountTokensRequest,
    AnthropicCountTokensResponse,
    AnthropicMessageRequest,
    AnthropicMessageResponse,
    AnthropicTextBlock,
    AnthropicToolChoice,
    AnthropicToolUseBlock,
    AnthropicUsage,
)

router = APIRouter(prefix="/v1", tags=["Compatibilidade Anthropic"])
logger = get_logger("nexa.compat.anthropic")

_SECURITY: dict[str, Any] = {"security": [{"HTTPBearer": []}]}

#: Margem da estimativa heurística de tokens (docs §4.7).
ESTIMATED_CHARS_PER_TOKEN = 4


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _message_id(request: Request) -> str:
    request_id = request_id_of(request).replace("-", "")
    return f"msg_nexa_{request_id[:24] or uuid4().hex[:24]}"


def _gateway_error(exc: GatewayError) -> CompatApiError:
    if isinstance(exc, ModelNotFound):
        return CompatApiError("model_not_found")
    if isinstance(exc, ModelNotAllowed):
        return CompatApiError("model_not_allowed")
    if isinstance(exc, UnsupportedCapability):
        param = "tools" if exc.capability == "tools" else "model"
        return CompatApiError("unsupported_capability", param=param)
    return CompatApiError(exc.code)


def _block_text(block: AnthropicContentBlock, *, field: str) -> str:
    if block.type != "text":
        raise CompatApiError(
            "bad_request",
            param=f"{field}.type",
            message=f"Bloco '{block.type}' não é aceito em {field}.",
        )
    if not isinstance(block.text, str):
        raise CompatApiError(
            "invalid_value",
            param=f"{field}.text",
            message="O bloco text exige o campo text.",
        )
    return block.text


def _system_text(
    system: str | list[AnthropicContentBlock] | None,
) -> str:
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    parts = [_block_text(block, field=f"system[{index}]") for index, block in enumerate(system)]
    return "\n\n".join(part for part in parts if part)


def _tool_result_text(block: AnthropicContentBlock, *, field: str) -> str:
    content = block.content
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for index, item in enumerate(content):
        item_type = item.get("type") if isinstance(item, dict) else None
        if item_type == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif item_type == "image":
            parts.append("[imagem]")
        else:
            raise CompatApiError(
                "bad_request",
                param=f"{field}.content[{index}].type",
                message="Blocos de tool_result aceitam apenas text ou image.",
            )
    return "".join(parts)


def _tool_call(block: AnthropicContentBlock, *, field: str) -> ToolCall:
    if not block.id or not block.name:
        raise CompatApiError(
            "invalid_value",
            param=field,
            message="Blocos tool_use exigem id e name.",
        )
    arguments = block.input if isinstance(block.input, dict) else {}
    return ToolCall(id=block.id, name=block.name, arguments=arguments)


def _tool_choice(value: AnthropicToolChoice | None) -> str | None:
    if value is None:
        return None
    if value.type == "any":
        return "required"
    if value.type == "tool":
        if not value.name:
            raise CompatApiError(
                "invalid_value",
                param="tool_choice.name",
                message="tool_choice do tipo tool exige name.",
            )
        return value.name
    return value.type


def _flush_user_text(ordered: list[ChatMessage], pending: list[str]) -> None:
    """Fecha o trecho de texto corrente preservando a ordem dos blocos."""
    if pending:
        ordered.append(ChatMessage(role="user", content="".join(pending)))
        pending.clear()


def _to_internal(
    payload: AnthropicMessageRequest | AnthropicCountTokensRequest,
    settings: Settings,
) -> tuple[list[ChatMessage], list[ToolSpec] | None, str | None, bool]:
    """Traduz o contrato Anthropic para mensagens/ferramentas internas."""
    context: list[ChatMessage] = []
    if settings.default_system_prompt:
        context.append(ChatMessage(role="system", content=settings.default_system_prompt))

    system = _system_text(payload.system)
    if system:
        context.append(ChatMessage(role="system", content=system))

    has_images = False
    for index, message in enumerate(payload.messages):
        field = f"messages[{index}].content"
        if isinstance(message.content, str):
            context.append(ChatMessage(role=message.role, content=message.content))
            continue

        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        ordered: list[ChatMessage] = []
        pending_text: list[str] = []

        for block_index, block in enumerate(message.content):
            block_field = f"{field}[{block_index}]"
            if not block.is_supported():
                raise CompatApiError(
                    "bad_request",
                    param=f"{block_field}.type",
                    message=f"Bloco de conteúdo '{block.type}' não suportado.",
                )
            if block.type == "text":
                value = _block_text(block, field=block_field)
                if message.role == "user":
                    pending_text.append(value)
                else:
                    texts.append(value)
            elif block.type == "image":
                has_images = True
                if message.role == "user":
                    pending_text.append("[imagem]")
                else:
                    texts.append("[imagem]")
            elif block.type == "tool_use":
                if message.role != "assistant":
                    raise CompatApiError(
                        "invalid_value",
                        param=block_field,
                        message="Blocos tool_use só são aceitos em mensagens assistant.",
                    )
                tool_calls.append(_tool_call(block, field=block_field))
            else:
                if message.role != "user":
                    raise CompatApiError(
                        "invalid_value",
                        param=block_field,
                        message="Blocos tool_result só são aceitos em mensagens user.",
                    )
                if not block.tool_use_id:
                    raise CompatApiError(
                        "invalid_value",
                        param=block_field,
                        message="Blocos tool_result exigem tool_use_id.",
                    )
                _flush_user_text(ordered, pending_text)
                ordered.append(
                    ChatMessage(
                        role="tool",
                        content=_tool_result_text(block, field=block_field),
                        tool_call_id=block.tool_use_id,
                    )
                )

        if message.role == "user":
            _flush_user_text(ordered, pending_text)
            context.extend(ordered)
        else:
            if not texts and not tool_calls:
                raise CompatApiError(
                    "invalid_value",
                    param=field,
                    message="Mensagens assistant exigem text ou tool_use.",
                )
            context.append(
                ChatMessage(
                    role="assistant",
                    content="".join(texts),
                    tool_calls=tuple(tool_calls),
                )
            )

    tools: list[ToolSpec] | None = None
    if payload.tools:
        tools = [
            ToolSpec(
                name=tool.name,
                description=tool.description,
                parameters=tool.input_schema,
            )
            for tool in payload.tools
        ]
    return context, tools, _tool_choice(payload.tool_choice), has_images


async def _resolve(
    request: Request,
    payload: AnthropicMessageRequest | AnthropicCountTokensRequest,
    *,
    stream: bool,
    tools: Sequence[ToolSpec] | None,
    has_images: bool,
) -> ResolvedModel:
    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    try:
        return await ai_router.resolve(
            payload.model,
            stream=stream,
            tools=tools,
            has_images=has_images,
            request_id=request_id_of(request),
        )
    except GatewayError as exc:
        raise _gateway_error(exc) from exc
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


def _usage(prompt_tokens: int | None, completion_tokens: int | None) -> AnthropicUsage:
    return AnthropicUsage(
        input_tokens=prompt_tokens or 0,
        output_tokens=completion_tokens or 0,
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


@router.post(
    "/messages",
    response_model=AnthropicMessageResponse,
    summary="Messages API no formato Anthropic",
    description=(
        "Traduz o contrato Anthropic (blocos de conteúdo, `system`, `tools`, "
        "`tool_use`) para o pipeline nativo. Devolve `message` (JSON) ou a sequência "
        "de eventos SSE tipados usada pelo Claude Code."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": "Mensagem JSON ou stream SSE tipado.",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        "event: message_start\n"
                        'data: {"type":"message_start","message":{"id":"msg_nexa_0192",'
                        '"type":"message","role":"assistant","model":"claude-sonnet-4-5",'
                        '"content":[],"stop_reason":null,"stop_sequence":null,'
                        '"usage":{"input_tokens":0,"output_tokens":0}}}\n\n'
                        "event: content_block_start\n"
                        'data: {"type":"content_block_start","index":0,'
                        '"content_block":{"type":"text","text":""}}\n\n'
                        "event: content_block_delta\n"
                        'data: {"type":"content_block_delta","index":0,'
                        '"delta":{"type":"text_delta","text":"Olá"}}\n\n'
                        "event: content_block_stop\n"
                        'data: {"type":"content_block_stop","index":0}\n\n'
                        "event: message_delta\n"
                        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn",'
                        '"stop_sequence":null},"usage":{"output_tokens":7}}\n\n'
                        "event: message_stop\n"
                        'data: {"type":"message_stop"}\n\n'
                    ),
                }
            },
        },
        status.HTTP_400_BAD_REQUEST: {"description": "Bloco ou parâmetro não suportado."},
        status.HTTP_401_UNAUTHORIZED: {"description": "API key ausente ou inválida."},
        status.HTTP_404_NOT_FOUND: {"description": "Modelo inexistente."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Requisição inválida."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit ou cota excedida."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Erro do provedor de IA."},
        status.HTTP_504_GATEWAY_TIMEOUT: {"description": "Timeout do provedor de IA."},
    },
    openapi_extra=_SECURITY,
)
async def create_message(
    payload: AnthropicMessageRequest,
    request: Request,
    response: Response,
) -> AnthropicMessageResponse | StreamingResponse:
    settings = _settings(request)
    identity = await get_compat_identity(request, scope=SCOPE_CHAT)
    ip = get_client_ip(request, settings.trusted_proxies)
    stats = cast(UsageStats, request.app.state.stats)
    stats.record_chat(subject=identity.subject, role=identity.role)

    context, tools, tool_choice, has_images = _to_internal(payload, settings)
    _validation_error(context, identity, settings)
    resolution = await _resolve(
        request,
        payload,
        stream=payload.stream,
        tools=tools,
        has_images=has_images,
    )
    limits = await enforce_compat_limits(request, identity, ip=ip, stats=stats)
    max_tokens = min(payload.max_tokens, settings.ai_max_output_tokens)
    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    message_id = _message_id(request)

    if payload.stream:
        stream_context = AnthropicStreamContext(
            message_id=message_id,
            model=resolution.model,
            router=ai_router,
            resolution=resolution,
            messages=context,
            max_tokens=max_tokens,
            max_seconds=settings.ai_max_output_seconds,
            request_id=request_id_of(request),
            tools=tools,
            tool_choice=tool_choice,
            idle_timeout_seconds=settings.ai_stream_idle_timeout_seconds,
            refund=_refund_callable(request, identity, ip=ip, limits=limits),
        )
        return StreamingResponse(
            anthropic_stream(stream_context),
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
        "compat_anthropic_completed",
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

    content: list[AnthropicTextBlock | AnthropicToolUseBlock] = []
    if result.content:
        content.append(AnthropicTextBlock(text=result.content))
    content.extend(
        AnthropicToolUseBlock(
            id=call.id,
            name=call.name,
            input=dict(call.arguments),
        )
        for call in result.tool_calls
    )
    if not content:
        content.append(AnthropicTextBlock(text=""))

    return AnthropicMessageResponse(
        id=message_id,
        model=routed.resolution.model,
        content=content,
        stop_reason=anthropic_stop_reason(result.finish_reason),
        usage=_usage(result.prompt_tokens, result.completion_tokens),
    )


def estimate_tokens(
    context: Sequence[ChatMessage],
    tools: Sequence[ToolSpec] | None = None,
) -> int:
    """Estimativa documentada: ~4 caracteres por token (docs §4.7)."""
    payload = "\n".join(message.content for message in context)
    if tools:
        payload += "".join(
            f"{tool.name}{tool.description or ''}{tool.parameters}" for tool in tools
        )
    return max(1, math.ceil(len(payload) / ESTIMATED_CHARS_PER_TOKEN))


@router.post(
    "/messages/count_tokens",
    response_model=AnthropicCountTokensResponse,
    summary="Contagem de tokens no formato Anthropic",
    description=(
        "Conta os tokens de entrada usando `count_tokens` do provedor quando "
        "disponível; caso contrário, aplica a estimativa documentada de ~4 "
        "caracteres por token. Não consome cota."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Contagem de tokens de entrada."},
        status.HTTP_400_BAD_REQUEST: {"description": "Bloco ou parâmetro não suportado."},
        status.HTTP_401_UNAUTHORIZED: {"description": "API key ausente ou inválida."},
        status.HTTP_404_NOT_FOUND: {"description": "Modelo inexistente."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Requisição inválida."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit da API key excedido."},
    },
    openapi_extra=_SECURITY,
)
async def count_tokens(
    payload: AnthropicCountTokensRequest,
    request: Request,
    response: Response,
) -> AnthropicCountTokensResponse:
    settings = _settings(request)
    identity = await get_compat_identity(request, scope=SCOPE_CHAT)
    ip = get_client_ip(request, settings.trusted_proxies)

    context, tools, _tool_choice_value, has_images = _to_internal(payload, settings)
    _validation_error(context, identity, settings)
    resolution = await _resolve(
        request,
        payload,
        stream=False,
        tools=None,
        has_images=has_images,
    )

    if identity.kind == "api_key":
        # A contagem não consome cota diária, mas respeita o limite da chave.
        limits = await enforce_compat_limits(request, identity, ip=ip)
        for name, value in limits.headers.items():
            response.headers[name] = value

    try:
        counted = await resolution.provider.count_tokens(context, model=resolution.model)
    except ProviderError:
        counted = None

    return AnthropicCountTokensResponse(
        input_tokens=counted if counted is not None else estimate_tokens(context, tools)
    )


__all__ = ["estimate_tokens", "router"]

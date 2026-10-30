"""Streaming SSE no contrato Anthropic (eventos tipados, F15/CA-35)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from app.ai.base import ChatMessage, FinishReason, GenerationChunk, ToolSpec
from app.ai.router import ProviderRouter, ResolvedModel, RoutedChunk
from app.compat.errors import CompatApiError
from app.core.sse import format_event
from app.schemas.compat_anthropic import AnthropicStopReason

#: Buffer entre o roteador e o cliente; limita memória e aplica backpressure.
STREAM_QUEUE_SIZE = 32


def anthropic_stop_reason(reason: FinishReason) -> AnthropicStopReason:
    """`stop_reason` interno → Anthropic (`end_turn`, `max_tokens`, `tool_use`)."""
    if reason == "tool_use":
        return "tool_use"
    if reason in {"length", "timeout"}:
        return "max_tokens"
    return "end_turn"


@dataclass(frozen=True, slots=True)
class AnthropicStreamContext:
    """Dados necessários para emitir o stream, sem depender da rota."""

    message_id: str
    model: str
    router: ProviderRouter
    resolution: ResolvedModel
    messages: Sequence[ChatMessage]
    max_tokens: int
    max_seconds: float
    request_id: str
    tools: Sequence[ToolSpec] | None = None
    tool_choice: str | None = None
    idle_timeout_seconds: float = 60.0
    refund: Callable[[], Awaitable[None]] | None = None


def _event(name: str, payload: dict[str, Any]) -> str:
    return format_event(name, payload)


def _message_start(context: AnthropicStreamContext) -> str:
    return _event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": context.message_id,
                "type": "message",
                "role": "assistant",
                "model": context.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )


def _content_block_start(index: int, block: dict[str, Any]) -> str:
    return _event(
        "content_block_start",
        {"type": "content_block_start", "index": index, "content_block": block},
    )


def _content_block_delta(index: int, delta: dict[str, Any]) -> str:
    return _event(
        "content_block_delta",
        {"type": "content_block_delta", "index": index, "delta": delta},
    )


def _content_block_stop(index: int) -> str:
    return _event("content_block_stop", {"type": "content_block_stop", "index": index})


def _message_delta(reason: AnthropicStopReason, output_tokens: int) -> str:
    return _event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
    )


def _message_stop() -> str:
    return _event("message_stop", {"type": "message_stop"})


def _error_event(code: str) -> str:
    return _event("error", CompatApiError(code).as_anthropic_payload())


async def _refund(context: AnthropicStreamContext) -> None:
    if context.refund is None:
        return

    async def invoke() -> None:
        assert context.refund is not None
        await context.refund()

    task: asyncio.Task[None] = asyncio.create_task(invoke())
    with suppress(asyncio.CancelledError):
        await asyncio.shield(task)


def _tool_events(index: int, chunk: GenerationChunk) -> list[str]:
    events: list[str] = []
    current = index
    for call in chunk.tool_calls:
        events.append(
            _content_block_start(
                current,
                {"type": "tool_use", "id": call.id, "name": call.name, "input": {}},
            )
        )
        events.append(
            _content_block_delta(
                current,
                {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(
                        dict(call.arguments),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            )
        )
        events.append(_content_block_stop(current))
        current += 1
    return events


async def anthropic_stream(context: AnthropicStreamContext) -> AsyncIterator[str]:
    """Emite a sequência tipada da Anthropic e encerra com `message_stop`."""
    queue: asyncio.Queue[RoutedChunk | BaseException | None] = asyncio.Queue(
        maxsize=STREAM_QUEUE_SIZE
    )
    iterator = context.router.stream(
        context.resolution,
        context.messages,
        max_tokens=context.max_tokens,
        max_seconds=context.max_seconds,
        request_id=context.request_id or None,
        tools=context.tools,
        tool_choice=context.tool_choice,
    )
    producer = asyncio.create_task(_pump(iterator, queue))
    last_activity = time.monotonic()
    delivered = False
    settled = False
    finish_reason: FinishReason | None = None
    completion_tokens: int | None = None
    block_index = 0
    text_index: int | None = None
    opened_any = False

    try:
        yield _message_start(context)

        while True:
            timeout = max(context.idle_timeout_seconds - (time.monotonic() - last_activity), 0.0)
            try:
                item = await asyncio.wait_for(queue.get(), timeout)
            except TimeoutError:
                if not delivered:
                    await _refund(context)
                settled = True
                yield _error_event("ai_provider_timeout")
                return

            last_activity = time.monotonic()

            if item is None:
                break
            if isinstance(item, BaseException):
                if not delivered:
                    await _refund(context)
                settled = True
                yield _error_event(_error_code(item))
                return

            chunk = item.chunk
            if chunk.completion_tokens is not None:
                completion_tokens = chunk.completion_tokens

            if chunk.content:
                if text_index is None:
                    text_index = block_index
                    block_index += 1
                    opened_any = True
                    yield _content_block_start(text_index, {"type": "text", "text": ""})
                delivered = True
                settled = True
                yield _content_block_delta(
                    text_index,
                    {"type": "text_delta", "text": chunk.content},
                )

            if chunk.tool_calls:
                if text_index is not None:
                    yield _content_block_stop(text_index)
                    text_index = None
                events = _tool_events(block_index, chunk)
                block_index += len(chunk.tool_calls)
                opened_any = True
                for event in events:
                    yield event
                delivered = True
                settled = True

            if chunk.finish_reason is not None:
                finish_reason = chunk.finish_reason
                settled = True
                break

        if finish_reason is None:
            if not delivered:
                await _refund(context)
            settled = True
            yield _error_event("ai_provider_error")
            return

        if text_index is not None:
            yield _content_block_stop(text_index)
            text_index = None
        if not opened_any:
            # Requisições sem conteúdo ainda precisam de um bloco válido.
            yield _content_block_start(0, {"type": "text", "text": ""})
            yield _content_block_stop(0)

        yield _message_delta(
            anthropic_stop_reason(finish_reason),
            completion_tokens or 0,
        )
        yield _message_stop()
    finally:
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
        close = getattr(iterator, "aclose", None)
        if close is not None:
            with suppress(asyncio.CancelledError):
                await close()
        if not settled:
            # Desconexão antes do primeiro trecho: devolve a cota consumida.
            await _refund(context)


def _error_code(exc: BaseException) -> str:
    from app.ai.base import GatewayError, ProviderTimeout, ProviderUnavailable

    if isinstance(exc, GatewayError):
        return exc.code
    if isinstance(exc, ProviderTimeout):
        return "ai_provider_timeout"
    if isinstance(exc, ProviderUnavailable):
        return "provider_unavailable"
    return "ai_provider_error"


async def _pump(
    iterator: AsyncIterator[RoutedChunk],
    queue: asyncio.Queue[RoutedChunk | BaseException | None],
) -> None:
    try:
        async for chunk in iterator:
            queue.put_nowait(chunk)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        queue.put_nowait(exc)
    else:
        queue.put_nowait(None)


__all__ = [
    "STREAM_QUEUE_SIZE",
    "AnthropicStreamContext",
    "anthropic_stop_reason",
    "anthropic_stream",
]

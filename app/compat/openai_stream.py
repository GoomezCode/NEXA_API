"""Streaming SSE no contrato OpenAI (`chat.completion.chunk` + `[DONE]`)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from app.ai.base import ChatMessage, FinishReason, GenerationChunk
from app.ai.router import ProviderRouter, ResolvedModel, RoutedChunk
from app.compat.errors import CompatApiError
from app.core.sse import format_data
from app.schemas.compat_openai import OpenAIFinishReason

#: Buffer entre o roteador e o cliente; limita memória e aplica backpressure.
STREAM_QUEUE_SIZE = 32

DONE = "data: [DONE]\n\n"


def openai_finish_reason(reason: FinishReason) -> OpenAIFinishReason:
    """`finish_reason` interno → OpenAI (`tool_calls`, `length`, `stop`)."""
    if reason == "tool_use":
        return "tool_calls"
    if reason == "timeout":
        return "length"
    return reason


@dataclass(frozen=True, slots=True)
class OpenAIStreamContext:
    """Dados necessários para emitir o stream, sem depender da rota."""

    completion_id: str
    created: int
    model: str
    router: ProviderRouter
    resolution: ResolvedModel
    messages: Sequence[ChatMessage]
    max_tokens: int
    max_seconds: float
    request_id: str
    include_usage: bool = False
    idle_timeout_seconds: float = 60.0
    refund: Callable[[], Awaitable[None]] | None = None


def _payload(
    context: OpenAIStreamContext,
    *,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": context.completion_id,
        "object": "chat.completion.chunk",
        "created": context.created,
        "model": context.model,
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish_reason},
        ],
    }
    if usage is not None:
        payload["choices"] = []
        payload["usage"] = usage
    return payload


def _tool_deltas(chunk: GenerationChunk) -> list[dict[str, Any]]:
    from app.schemas.compat_openai import dump_arguments

    return [
        {
            "index": index,
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": dump_arguments(dict(call.arguments)),
            },
        }
        for index, call in enumerate(chunk.tool_calls)
    ]


async def _refund(context: OpenAIStreamContext) -> None:
    if context.refund is None:
        return

    async def invoke() -> None:
        assert context.refund is not None
        await context.refund()

    task: asyncio.Task[None] = asyncio.create_task(invoke())
    with suppress(asyncio.CancelledError):
        await asyncio.shield(task)


def _error_data(code: str) -> str:
    return format_data(CompatApiError(code).as_payload())


async def openai_stream(context: OpenAIStreamContext) -> AsyncIterator[str]:
    """Emite os chunks OpenAI, erros no envelope e o terminador `[DONE]`."""
    queue: asyncio.Queue[RoutedChunk | BaseException | None] = asyncio.Queue(
        maxsize=STREAM_QUEUE_SIZE
    )
    iterator = context.router.stream(
        context.resolution,
        context.messages,
        max_tokens=context.max_tokens,
        max_seconds=context.max_seconds,
        request_id=context.request_id or None,
    )
    producer = asyncio.create_task(_pump(iterator, queue))
    last_activity = time.monotonic()
    delivered = False
    settled = False
    finish_reason: FinishReason | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    try:
        yield format_data(_payload(context, delta={"role": "assistant", "content": ""}))

        while True:
            timeout = max(context.idle_timeout_seconds - (time.monotonic() - last_activity), 0.0)
            try:
                item = await asyncio.wait_for(queue.get(), timeout)
            except TimeoutError:
                if not delivered:
                    await _refund(context)
                settled = True
                yield _error_data("ai_provider_timeout")
                return

            last_activity = time.monotonic()

            if item is None:
                break
            if isinstance(item, BaseException):
                if not delivered:
                    await _refund(context)
                settled = True
                yield _error_data(_error_code(item))
                return

            chunk = item.chunk
            if chunk.prompt_tokens is not None:
                prompt_tokens = chunk.prompt_tokens
            if chunk.completion_tokens is not None:
                completion_tokens = chunk.completion_tokens
            if chunk.content:
                delivered = True
                settled = True
                yield format_data(_payload(context, delta={"content": chunk.content}))
            if chunk.tool_calls:
                delivered = True
                settled = True
                yield format_data(_payload(context, delta={"tool_calls": _tool_deltas(chunk)}))
            if chunk.finish_reason is not None:
                finish_reason = chunk.finish_reason
                settled = True
                break

        if finish_reason is None:
            if not delivered:
                await _refund(context)
            settled = True
            yield _error_data("ai_provider_error")
            return

        yield format_data(
            _payload(
                context,
                delta={},
                finish_reason=openai_finish_reason(finish_reason),
            )
        )
        if context.include_usage:
            prompt = prompt_tokens or 0
            completion = completion_tokens or 0
            yield format_data(
                _payload(
                    context,
                    delta={},
                    usage={
                        "prompt_tokens": prompt,
                        "completion_tokens": completion,
                        "total_tokens": prompt + completion,
                    },
                )
            )
        yield DONE
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
    "DONE",
    "STREAM_QUEUE_SIZE",
    "OpenAIStreamContext",
    "openai_finish_reason",
    "openai_stream",
]

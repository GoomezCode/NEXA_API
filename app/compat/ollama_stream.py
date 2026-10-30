"""Streaming NDJSON no contrato Ollama (`done:true` na última linha, F16)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from app.ai.base import ChatMessage, FinishReason, ToolCall, ToolSpec
from app.ai.router import ProviderRouter, ResolvedModel, RoutedChunk
from app.compat.errors import CompatApiError
from app.schemas.compat_ollama import OllamaDoneReason, OllamaToolCallFunction, OllamaToolCallOut

#: Buffer entre o roteador e o cliente; limita memória e aplica backpressure.
STREAM_QUEUE_SIZE = 32

#: Cabeçalhos do streaming NDJSON.
NDJSON_HEADERS: dict[str, str] = {
    "Content-Type": "application/x-ndjson",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def ollama_done_reason(reason: FinishReason) -> OllamaDoneReason:
    """`done_reason` interno → Ollama (`tool_calls`, `length`, `stop`)."""
    if reason == "tool_use":
        return "tool_calls"
    if reason in {"length", "timeout"}:
        return "length"
    return "stop"


def format_ndjson(payload: dict[str, Any]) -> str:
    """Uma linha JSON terminada em `\\n` (NDJSON)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def ollama_tool_calls(calls: Sequence[ToolCall]) -> list[OllamaToolCallOut]:
    return [
        OllamaToolCallOut(
            function=OllamaToolCallFunction(
                name=call.name,
                arguments=dict(call.arguments),
            )
        )
        for call in calls
    ]


@dataclass(frozen=True, slots=True)
class OllamaStreamContext:
    """Dados necessários para emitir o stream, sem depender da rota."""

    model: str
    created_at: str
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


def _payload(
    context: OllamaStreamContext,
    *,
    content: str = "",
    tool_calls: Sequence[ToolCall] = (),
    done: bool = False,
    done_reason: OllamaDoneReason | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            call.model_dump(mode="json") for call in ollama_tool_calls(tool_calls)
        ]
    payload: dict[str, Any] = {
        "model": context.model,
        "created_at": context.created_at,
        "message": message,
        "done": done,
    }
    if done:
        payload["done_reason"] = done_reason
        payload["prompt_eval_count"] = prompt_tokens or 0
        payload["eval_count"] = completion_tokens or 0
    return payload


def _error_line(code: str) -> str:
    return format_ndjson({"error": CompatApiError(code).message})


async def _refund(context: OllamaStreamContext) -> None:
    if context.refund is None:
        return

    async def invoke() -> None:
        assert context.refund is not None
        await context.refund()

    task: asyncio.Task[None] = asyncio.create_task(invoke())
    with suppress(asyncio.CancelledError):
        await asyncio.shield(task)


async def ollama_stream(context: OllamaStreamContext) -> AsyncIterator[str]:
    """Emite linhas NDJSON e encerra com `done:true` ou `{"error":...}`."""
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
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tool_calls: list[ToolCall] = []

    try:
        while True:
            timeout = max(context.idle_timeout_seconds - (time.monotonic() - last_activity), 0.0)
            try:
                item = await asyncio.wait_for(queue.get(), timeout)
            except TimeoutError:
                if not delivered:
                    await _refund(context)
                settled = True
                yield _error_line("ai_provider_timeout")
                return

            last_activity = time.monotonic()

            if item is None:
                break
            if isinstance(item, BaseException):
                if not delivered:
                    await _refund(context)
                settled = True
                yield _error_line(_error_code(item))
                return

            chunk = item.chunk
            if chunk.prompt_tokens is not None:
                prompt_tokens = chunk.prompt_tokens
            if chunk.completion_tokens is not None:
                completion_tokens = chunk.completion_tokens
            if chunk.tool_calls:
                tool_calls.extend(chunk.tool_calls)

            if chunk.content:
                delivered = True
                settled = True
                yield format_ndjson(_payload(context, content=chunk.content))

            if chunk.finish_reason is not None:
                finish_reason = chunk.finish_reason
                settled = True
                break

        if finish_reason is None:
            if not delivered:
                await _refund(context)
            settled = True
            yield _error_line("ai_provider_error")
            return

        yield format_ndjson(
            _payload(
                context,
                done=True,
                done_reason=ollama_done_reason(finish_reason),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tool_calls=tool_calls,
            )
        )
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
    "NDJSON_HEADERS",
    "STREAM_QUEUE_SIZE",
    "OllamaStreamContext",
    "format_ndjson",
    "ollama_done_reason",
    "ollama_stream",
    "ollama_tool_calls",
]

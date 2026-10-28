"""Streaming SSE do chat: heartbeat, conclusão, erros e devolução de cota.

O provedor é consumido por uma task separada que publica os trechos numa fila.
Assim o gerador de SSE consegue emitir `: ping` em períodos de inatividade sem
cancelar a leitura em andamento (RF-CHAT-03), e o cancelamento do cliente
encerra a iteração e libera a conexão com o provedor (RF-CHAT-07).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass

from app.ai.base import (
    AIProvider,
    ChatMessage,
    FinishReason,
    GenerationChunk,
    ProviderTimeout,
)
from app.core.errors import spec_for
from app.core.logging import get_logger
from app.core.security import Role
from app.core.sse import HEARTBEAT, HEARTBEAT_INTERVAL_SECONDS, format_data, format_event
from app.core.stats import UsageStats
from app.core.usage_store import UsageStore
from app.schemas.chat import StreamChunk, StreamDone, StreamError
from app.schemas.usage import UsageInfo

logger = get_logger("nexa.chat")

#: Inatividade máxima no stream antes de abortar (RNF-CON-01).
DEFAULT_IDLE_TIMEOUT_SECONDS = 60.0

#: Buffer entre o provedor e o cliente; limita memória e aplica backpressure.
STREAM_QUEUE_SIZE = 32


@dataclass(frozen=True, slots=True)
class StreamSession:
    """Tudo que o fluxo SSE precisa, sem depender da rota."""

    provider: AIProvider
    messages: Sequence[ChatMessage]
    usage: UsageInfo
    store: UsageStore
    role: Role
    subject: str
    ip: str
    max_tokens: int
    max_seconds: float
    request_id: str
    heartbeat_seconds: float = HEARTBEAT_INTERVAL_SECONDS
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS
    stats: UsageStats | None = None


def error_code(exc: BaseException) -> str:
    """Código Problem Details correspondente à falha do provedor."""
    return "ai_provider_timeout" if isinstance(exc, ProviderTimeout) else "ai_provider_error"


async def stream_events(session: StreamSession) -> AsyncIterator[str]:
    """Gera os eventos SSE; devolve a cota se nada foi entregue."""
    queue: asyncio.Queue[GenerationChunk | BaseException | None] = asyncio.Queue(
        maxsize=STREAM_QUEUE_SIZE
    )
    iterator = session.provider.stream(
        session.messages,
        max_tokens=session.max_tokens,
        max_seconds=session.max_seconds,
        request_id=session.request_id or None,
    )
    producer = asyncio.create_task(_pump(iterator, queue))
    started = time.perf_counter()
    last_activity = time.monotonic()
    delivered = False
    settled = False
    finish_reason: FinishReason | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    try:
        while True:
            idle_left = session.idle_timeout_seconds - (time.monotonic() - last_activity)
            try:
                item = await asyncio.wait_for(
                    queue.get(), min(session.heartbeat_seconds, max(idle_left, 0.0))
                )
            except TimeoutError:
                if time.monotonic() - last_activity >= session.idle_timeout_seconds:
                    # RNF-CON-01: o provedor ficou inativo por tempo demais.
                    if not delivered:
                        await _refund(session)
                    settled = True
                    yield _error_event(session, "ai_provider_timeout")
                    _record_failure(session, "ai_provider_timeout", started)
                    return
                yield HEARTBEAT
                continue

            last_activity = time.monotonic()

            if item is None:
                break
            if isinstance(item, BaseException):
                if not delivered:
                    await _refund(session)
                settled = True
                yield _error_event(session, error_code(item))
                _record_failure(session, error_code(item), started)
                return

            chunk: GenerationChunk = item
            if chunk.prompt_tokens is not None:
                prompt_tokens = chunk.prompt_tokens
            if chunk.completion_tokens is not None:
                completion_tokens = chunk.completion_tokens
            if chunk.content:
                # Primeiro trecho entregue: o consumo passa a ser definitivo.
                delivered = True
                settled = True
                yield format_data(StreamChunk(content=chunk.content).model_dump(mode="json"))
            if chunk.finish_reason is not None:
                finish_reason = chunk.finish_reason
                settled = True
                break

        if finish_reason is None:
            # O provedor encerrou sem o evento de conclusão.
            if not delivered:
                await _refund(session)
            settled = True
            yield _error_event(session, "ai_provider_error")
            _record_failure(session, "ai_provider_error", started)
            return

        yield format_event(
            "done",
            StreamDone(finish_reason=finish_reason, usage=session.usage).model_dump(mode="json"),
        )
        if session.stats is not None:
            session.stats.record_duration(time.perf_counter() - started)
        logger.info(
            "chat_stream_completed",
            role=session.role,
            provider=session.provider.name,
            model=session.provider.model,
            finish_reason=finish_reason,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
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
            # Desconexão antes do primeiro trecho (ou fechamento do gerador).
            # O refund roda em task própria: sob cancelamento do cliente os
            # `await` deste bloco podem ser interrompidos, mas a task segue.
            refund = asyncio.create_task(_refund(session))
            with suppress(asyncio.CancelledError):
                await asyncio.shield(refund)
            logger.info(
                "chat_stream_aborted",
                role=session.role,
                provider=session.provider.name,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )


async def _pump(
    iterator: AsyncIterator[GenerationChunk],
    queue: asyncio.Queue[GenerationChunk | BaseException | None],
) -> None:
    """Consome o provedor e publica trechos, falhas e o fim na fila."""
    try:
        async for chunk in iterator:
            queue.put_nowait(chunk)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        queue.put_nowait(exc)
    else:
        queue.put_nowait(None)


async def _refund(session: StreamSession) -> None:
    """Devolve 1 unidade de cota (gid e IP) ao guest."""
    await session.store.refund(role=session.role, subject=session.subject, ip=session.ip)


def _error_event(session: StreamSession, code: str) -> str:
    payload = StreamError(
        code=code,
        detail=spec_for(code).detail,
        request_id=session.request_id,
    )
    return format_event("error", payload.model_dump(mode="json"))


def _record_failure(session: StreamSession, code: str, started: float) -> None:
    """Estatísticas RF-ADM-02 + log de falha do provedor no streaming."""
    if session.stats is not None:
        session.stats.record_provider_error()
        session.stats.record_duration(time.perf_counter() - started)
    _log_failure(session, code, started)


def _log_failure(session: StreamSession, code: str, started: float) -> None:
    logger.warning(
        "chat_stream_failed",
        role=session.role,
        provider=session.provider.name,
        code=code,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )


__all__ = [
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "STREAM_QUEUE_SIZE",
    "StreamSession",
    "error_code",
    "stream_events",
]

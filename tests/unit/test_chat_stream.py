"""Testes do gerador SSE do chat (RF-CHAT-03/07/08/12)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.ai.base import (
    ChatMessage,
    GenerationChunk,
    GenerationResult,
    ProviderError,
    ProviderTimeout,
)
from app.ai.fake import FakeProvider
from app.core.chat_stream import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    StreamSession,
    error_code,
    stream_events,
)
from app.core.usage_store import InMemoryUsageStore
from tests.conftest import FakeClock, make_settings, parse_sse

IP = "203.0.113.10"
GID = "gid-stream"
REQUEST_ID = "req-1"


@dataclass
class ScriptedProvider:
    """Provedor com roteiro: trechos, exceções e pontos de bloqueio."""

    script: list[object]
    name: str = "scripted"
    model: str = "scripted-model"
    closed: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[GenerationChunk]:
        self.calls.append(
            {
                "messages": list(messages),
                "max_tokens": max_tokens,
                "max_seconds": max_seconds,
                "request_id": request_id,
                "model": model,
            }
        )
        try:
            for item in self.script:
                if isinstance(item, asyncio.Event):
                    await item.wait()
                elif isinstance(item, BaseException):
                    raise item
                else:
                    yield item  # type: ignore[misc]
        finally:
            self.closed = True

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
    ) -> GenerationResult:
        raise NotImplementedError

    async def health(self) -> bool:
        return True


async def make_session(
    provider: object,
    *,
    heartbeat_seconds: float = 15.0,
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    role: str = "guest",
    **overrides: object,
) -> tuple[StreamSession, InMemoryUsageStore]:
    store = InMemoryUsageStore(make_settings(**overrides), FakeClock())
    decision = await store.consume(role=role, subject=GID, ip=IP)
    assert decision.allowed
    session = StreamSession(
        provider=provider,  # type: ignore[arg-type]
        messages=[ChatMessage(role="user", content="oi")],
        usage=decision.usage,
        store=store,
        role=role,  # type: ignore[arg-type]
        subject=GID,
        ip=IP,
        max_tokens=128,
        max_seconds=30.0,
        request_id=REQUEST_ID,
        model="scripted-model",
        heartbeat_seconds=heartbeat_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
    )
    return session, store


async def collect(session: StreamSession) -> list[str]:
    return [event async for event in stream_events(session)]


async def remaining(store: InMemoryUsageStore) -> int | None:
    usage = await store.snapshot(role="guest", subject=GID, ip=IP)
    return usage.daily_remaining


async def test_stream_yields_data_chunks_then_done() -> None:
    provider = ScriptedProvider(
        [
            GenerationChunk("Olá"),
            GenerationChunk(" mundo"),
            GenerationChunk(finish_reason="stop", prompt_tokens=5, completion_tokens=2),
        ]
    )
    session, store = await make_session(provider)

    events = parse_sse("".join(await collect(session)))

    contents = [
        event.data["content"]
        for event in events
        if event.data is not None and "content" in event.data
    ]
    assert contents == ["Olá", " mundo"]
    assert events[-1].event == "done"
    assert events[-1].data == {
        "status": "complete",
        "finish_reason": "stop",
        "key_source": "server",
        "usage": {
            "daily_limit": 10,
            "daily_remaining": 9,
            "per_minute_limit": 3,
            "per_minute_remaining": 2,
            "resets_at": "2026-10-29T00:00:00Z",
        },
    }
    assert provider.closed is True
    assert provider.calls[0]["request_id"] == REQUEST_ID
    assert provider.calls[0]["max_tokens"] == 128
    assert provider.calls[0]["max_seconds"] == 30.0
    assert await remaining(store) == 9


async def test_finish_reason_length_and_timeout_are_forwarded() -> None:
    for finish_reason in ("length", "timeout"):
        provider = ScriptedProvider(
            [GenerationChunk("corte"), GenerationChunk(finish_reason=finish_reason)]
        )
        session, _ = await make_session(provider)

        events = parse_sse("".join(await collect(session)))

        assert events[-1].event == "done"
        assert events[-1].data is not None
        assert events[-1].data["finish_reason"] == finish_reason


async def test_completion_without_content_still_consumes_quota() -> None:
    provider = ScriptedProvider([GenerationChunk(finish_reason="timeout")])
    session, store = await make_session(provider)

    events = parse_sse("".join(await collect(session)))

    assert [event.event for event in events] == ["done"]
    assert events[0].data is not None
    assert events[0].data["finish_reason"] == "timeout"
    assert await remaining(store) == 9


async def test_heartbeat_is_sent_while_the_provider_is_idle() -> None:
    provider = FakeProvider(chunks=("a",), delay=0.03)
    session, _ = await make_session(provider, heartbeat_seconds=0.01)

    events = parse_sse("".join(await collect(session)))

    assert any(event.comment == "ping" for event in events)
    assert events[-1].event == "done"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderError("falhou"), "ai_provider_error"),
        (ProviderTimeout("lento"), "ai_provider_timeout"),
        (RuntimeError("bug"), "ai_provider_error"),
    ],
)
async def test_error_before_first_chunk_refunds_and_reports(
    error: BaseException, code: str
) -> None:
    provider = ScriptedProvider([error])
    session, store = await make_session(provider)

    events = parse_sse("".join(await collect(session)))

    assert [event.event for event in events] == ["error"]
    assert events[0].data is not None
    assert events[0].data["code"] == code
    assert events[0].data["request_id"] == REQUEST_ID
    assert await remaining(store) == 10


async def test_error_after_first_chunk_keeps_the_consumed_quota() -> None:
    provider = ScriptedProvider([GenerationChunk("parcial"), ProviderError("caiu no meio")])
    session, store = await make_session(provider)

    events = parse_sse("".join(await collect(session)))

    assert events[0].data == {"content": "parcial"}
    assert events[-1].event == "error"
    assert events[-1].data is not None
    assert events[-1].data["code"] == "ai_provider_error"
    assert await remaining(store) == 9


@pytest.mark.parametrize("content", ["", "parcial"])
async def test_stream_without_completion_event_is_an_error(content: str) -> None:
    provider = ScriptedProvider([GenerationChunk(content)])
    session, store = await make_session(provider)

    events = parse_sse("".join(await collect(session)))

    assert events[-1].event == "error"
    assert events[-1].data is not None
    assert events[-1].data["code"] == "ai_provider_error"
    assert await remaining(store) == (10 if not content else 9)


async def test_idle_provider_before_content_refunds_and_reports_timeout() -> None:
    gate = asyncio.Event()
    provider = ScriptedProvider([gate])
    session, store = await make_session(provider, heartbeat_seconds=0.01, idle_timeout_seconds=0.03)

    events = parse_sse("".join(await collect(session)))

    assert any(event.comment == "ping" for event in events)
    assert events[-1].event == "error"
    assert events[-1].data is not None
    assert events[-1].data["code"] == "ai_provider_timeout"
    assert await remaining(store) == 10
    assert provider.closed is True


async def test_idle_provider_after_content_keeps_the_quota() -> None:
    gate = asyncio.Event()
    provider = ScriptedProvider([GenerationChunk("parcial"), gate])
    session, store = await make_session(provider, heartbeat_seconds=0.01, idle_timeout_seconds=0.03)

    events = parse_sse("".join(await collect(session)))

    assert events[0].data == {"content": "parcial"}
    assert events[-1].event == "error"
    assert events[-1].data is not None
    assert events[-1].data["code"] == "ai_provider_timeout"
    assert await remaining(store) == 9


async def test_disconnect_before_first_chunk_refunds_and_closes_provider() -> None:
    gate = asyncio.Event()
    provider = ScriptedProvider([gate, GenerationChunk("nunca")])
    session, store = await make_session(provider, heartbeat_seconds=0.01)
    events = stream_events(session)

    assert await anext(events) == ": ping\n\n"
    await events.aclose()

    assert provider.closed is True
    assert await remaining(store) == 10


async def test_disconnect_after_first_chunk_keeps_the_quota() -> None:
    gate = asyncio.Event()
    provider = ScriptedProvider([GenerationChunk("parcial"), gate])
    session, store = await make_session(provider)
    events = stream_events(session)

    first = await anext(events)
    assert json.loads(first.removeprefix("data: ")) == {"content": "parcial"}
    await events.aclose()

    assert provider.closed is True
    assert await remaining(store) == 9


async def test_admin_completion_reports_null_daily_usage() -> None:
    provider = FakeProvider(chunks=("ok",))
    session, _ = await make_session(provider, role="admin", rate_limit_admin_chat_per_min=5)

    events = parse_sse("".join(await collect(session)))

    assert events[-1].event == "done"
    assert events[-1].data is not None
    assert events[-1].data["usage"]["daily_limit"] is None
    assert events[-1].data["usage"]["daily_remaining"] is None
    assert events[-1].data["usage"]["resets_at"] is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderTimeout("lento"), "ai_provider_timeout"),
        (ProviderError("falhou"), "ai_provider_error"),
        (RuntimeError("bug"), "ai_provider_error"),
    ],
)
def test_error_code_mapping(error: BaseException, expected: str) -> None:
    assert error_code(error) == expected

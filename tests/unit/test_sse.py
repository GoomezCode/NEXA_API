"""Testes da formatação SSE (RF-CHAT-03, SRS §3.7.3)."""

from __future__ import annotations

from app.core.sse import (
    HEARTBEAT,
    HEARTBEAT_INTERVAL_SECONDS,
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    format_data,
    format_event,
)
from tests.conftest import parse_sse


def test_format_data_keeps_multiline_content_in_one_field() -> None:
    text = format_data({"content": "linha 1\nlinha 2"})

    assert text.startswith("data: ")
    assert text.endswith("\n\n")
    assert text.count("\n\n") == 1
    events = parse_sse(text)
    assert events[0].event is None
    assert events[0].data == {"content": "linha 1\nlinha 2"}


def test_format_event_names_the_event() -> None:
    events = parse_sse(format_event("done", {"status": "complete", "stop": True}))

    assert len(events) == 1
    assert events[0].event == "done"
    assert events[0].data == {"status": "complete", "stop": True}


def test_heartbeat_is_a_comment() -> None:
    assert HEARTBEAT == ": ping\n\n"
    events = parse_sse(HEARTBEAT)
    assert events[0].comment == "ping"
    assert events[0].data is None


def test_heartbeat_interval_is_fifteen_seconds() -> None:
    assert HEARTBEAT_INTERVAL_SECONDS == 15.0


def test_sse_headers_contract() -> None:
    assert SSE_MEDIA_TYPE == "text/event-stream"
    assert SSE_HEADERS == {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

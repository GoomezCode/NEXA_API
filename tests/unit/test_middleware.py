"""Testes unitários dos middlewares ASGI (F0-06)."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from app.core.logging import current_request_id
from app.core.middleware import (
    BodyLimitMiddleware,
    ContentTypeMiddleware,
    RequestContextMiddleware,
)
from tests.conftest import make_settings


def http_scope(
    method: str = "POST",
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    state: dict[str, Any] | None = None,
) -> Scope:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": "/chat",
        "raw_path": b"/chat",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "state": state or {},
    }


class Recorder:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def send(self, message: Message) -> None:
        self.messages.append(message)

    @property
    def start(self) -> Message:
        return next(item for item in self.messages if item["type"] == "http.response.start")

    @property
    def body(self) -> Message:
        return next(item for item in self.messages if item["type"] == "http.response.body")


async def test_body_limit_reads_content_length() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = BodyLimitMiddleware(downstream, max_bytes=10)
    recorder = Recorder()

    async def receive() -> Message:  # pragma: no cover - não deve ser lido
        raise AssertionError("corpo não deveria ser lido")

    await middleware(http_scope(headers=[(b"content-length", b"11")]), receive, recorder.send)

    assert not called
    assert recorder.start["status"] == 413
    assert b"payload_too_large" in recorder.body["body"]


async def test_body_limit_allows_content_length_under_limit() -> None:
    seen: list[bytes] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        seen.append(message.get("body", b""))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodyLimitMiddleware(downstream, max_bytes=10)
    recorder = Recorder()
    chunks = [{"type": "http.request", "body": b"abc", "more_body": False}]

    async def receive() -> Message:
        return chunks.pop(0)

    await middleware(http_scope(headers=[(b"content-length", b"3")]), receive, recorder.send)

    assert seen == [b"abc"]
    assert recorder.start["status"] == 200


async def test_body_limit_buffers_requests_without_content_length() -> None:
    seen: list[bytes] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        seen.append(message.get("body", b""))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodyLimitMiddleware(downstream, max_bytes=10)
    recorder = Recorder()
    chunks = [
        {"type": "http.request", "body": b"abc", "more_body": True},
        {"type": "http.request", "body": b"de", "more_body": False},
    ]

    async def receive() -> Message:
        return chunks.pop(0)

    await middleware(http_scope(), receive, recorder.send)

    assert seen == [b"abcde"]
    assert recorder.start["status"] == 200


async def test_body_limit_rejects_chunked_body_over_limit() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = BodyLimitMiddleware(downstream, max_bytes=10)
    recorder = Recorder()
    chunks = [
        {"type": "http.request", "body": b"x" * 8, "more_body": True},
        {"type": "http.request", "body": b"x" * 8, "more_body": False},
    ]

    async def receive() -> Message:
        return chunks.pop(0)

    await middleware(http_scope(), receive, recorder.send)

    assert not called
    assert recorder.start["status"] == 413


async def test_content_type_allows_json_body() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = ContentTypeMiddleware(downstream)
    recorder = Recorder()
    scope = http_scope(
        headers=[
            (b"content-length", b"2"),
            (b"content-type", b"application/json; charset=utf-8"),
        ]
    )

    await middleware(scope, lambda: None, recorder.send)

    assert called
    assert recorder.messages == []


async def test_content_type_allows_json_suffix() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = ContentTypeMiddleware(downstream)
    scope = http_scope(
        headers=[
            (b"content-length", b"2"),
            (b"content-type", b"application/merge-patch+json"),
        ]
    )

    await middleware(scope, lambda: None, Recorder().send)

    assert called


async def test_content_type_rejects_non_json_body() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = ContentTypeMiddleware(downstream)
    recorder = Recorder()
    scope = http_scope(headers=[(b"content-length", b"2"), (b"content-type", b"text/plain")])

    await middleware(scope, lambda: None, recorder.send)

    assert not called
    assert recorder.start["status"] == 415
    assert b"unsupported_media_type" in recorder.body["body"]


async def test_content_type_rejects_body_without_header() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = ContentTypeMiddleware(downstream)
    recorder = Recorder()

    await middleware(http_scope(headers=[(b"content-length", b"2")]), lambda: None, recorder.send)

    assert not called
    assert recorder.start["status"] == 415


async def test_content_type_rejects_chunked_body_without_header() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = ContentTypeMiddleware(downstream)
    recorder = Recorder()
    scope = http_scope(headers=[(b"transfer-encoding", b"chunked")])

    await middleware(scope, lambda: None, recorder.send)

    assert not called
    assert recorder.start["status"] == 415


async def test_content_type_treats_malformed_length_as_body() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = ContentTypeMiddleware(downstream)
    recorder = Recorder()
    scope = http_scope(headers=[(b"content-length", b"oops")])

    await middleware(scope, lambda: None, recorder.send)

    assert not called
    assert recorder.start["status"] == 415


async def test_content_type_allows_requests_without_body() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = ContentTypeMiddleware(downstream)
    scope = http_scope(headers=[(b"content-length", b"0")])

    await middleware(scope, lambda: None, Recorder().send)

    assert called


async def test_body_limit_ignores_non_body_methods() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = BodyLimitMiddleware(downstream, max_bytes=1)
    recorder = Recorder()
    await middleware(
        http_scope("GET", headers=[(b"content-length", b"999")]),
        lambda: None,
        recorder.send,
    )
    assert called


async def test_body_limit_ignores_non_http_scopes() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = BodyLimitMiddleware(downstream, max_bytes=1)
    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert called


async def test_body_limit_treats_invalid_content_length_as_absent() -> None:
    seen: list[bytes] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        seen.append(message.get("body", b""))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = BodyLimitMiddleware(downstream, max_bytes=10)
    recorder = Recorder()
    chunks = [
        {"type": "http.request", "body": b"abc", "more_body": False},
    ]

    async def receive() -> Message:
        return chunks.pop(0)

    await middleware(http_scope(headers=[(b"content-length", b"abc")]), receive, recorder.send)
    assert seen == [b"abc"]


async def test_body_limit_stops_on_disconnect() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = BodyLimitMiddleware(downstream, max_bytes=10)
    recorder = Recorder()

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    await middleware(http_scope(), receive, recorder.send)
    assert not called
    assert recorder.messages == []


async def test_request_context_sets_request_id_from_header() -> None:
    seen: dict[str, Any] = {}

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        seen["state"] = scope["state"]["request_id"]
        seen["context"] = current_request_id()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestContextMiddleware(downstream, settings=make_settings())
    recorder = Recorder()
    request_id = "b1f4c7e2-6f0f-4c39-9e57-6a1f0f0b8f4d"

    await middleware(
        http_scope(headers=[(b"x-request-id", request_id.encode("latin-1"))]),
        None,  # type: ignore[arg-type]
        recorder.send,
    )

    assert seen["state"] == request_id
    assert seen["context"] == request_id
    assert dict(recorder.start["headers"])[b"x-request-id"] == request_id.encode("latin-1")
    assert current_request_id() == ""


async def test_request_context_generates_request_id() -> None:
    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestContextMiddleware(downstream, settings=make_settings())
    recorder = Recorder()
    await middleware(http_scope(), None, recorder.send)  # type: ignore[arg-type]

    generated = dict(recorder.start["headers"])[b"x-request-id"].decode()
    assert generated


async def test_request_context_adds_hsts_in_production() -> None:
    settings = make_settings(
        environment="production", jwt_secret="z" * 40, key_encryption_secret="k" * 48
    )

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestContextMiddleware(downstream, settings=settings)
    recorder = Recorder()
    await middleware(http_scope(), None, recorder.send)  # type: ignore[arg-type]

    assert b"strict-transport-security" in dict(recorder.start["headers"])


async def test_request_context_passes_non_http_scope() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    middleware = RequestContextMiddleware(downstream, settings=make_settings())
    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert called


async def test_request_context_logs_and_reraises() -> None:
    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("falha simulada")

    middleware = RequestContextMiddleware(downstream, settings=make_settings())
    with pytest.raises(RuntimeError):
        await middleware(http_scope(), None, middleware.app.__call__)  # type: ignore[arg-type]

    assert current_request_id() == ""

"""Middlewares ASGI: contexto/segurança e limite de corpo (RNF-OBS-01, RNF-SEG-09)."""

from __future__ import annotations

import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.core.errors import send_payload_too_large, send_unsupported_media_type
from app.core.logging import bind_request_id, get_logger, reset_request_id

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_STATIC_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


def _header(scope: Scope, name: bytes) -> str | None:
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, list):
        return None
    for key, value in raw_headers:
        if not isinstance(key, bytes) or not isinstance(value, bytes):
            continue
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _route_path(scope: Scope) -> str:
    """Rota resolvida (template); cai no caminho bruto quando não houve match."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else scope.get("path", "")


def _normalize_request_id(value: str | None) -> str:
    """Aceita somente UUID válido; caso contrário gera um novo (RF-SIS-03)."""
    if value:
        try:
            return str(uuid.UUID(value.strip()))
        except (ValueError, AttributeError):
            pass
    return str(uuid.uuid4())


class RequestContextMiddleware:
    """Propaga `X-Request-ID`, registra acesso e adiciona cabeçalhos de segurança."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.logger = get_logger("nexa.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _normalize_request_id(_header(scope, b"x-request-id"))
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id
        token = bind_request_id(request_id)
        start = time.perf_counter()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in headers}
                if b"x-request-id" not in existing:
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                for key, value in _STATIC_SECURITY_HEADERS:
                    if key not in existing:
                        headers.append((key, value))
                if self.settings.is_production and b"strict-transport-security" not in existing:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message = {**message, "headers": headers}
                self.logger.info(
                    "http_request",
                    method=scope.get("method", ""),
                    path=scope.get("path", ""),
                    route=_route_path(scope),
                    status=message.get("status", 0),
                    duration_ms=round((time.perf_counter() - start) * 1000, 2),
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            self.logger.exception(
                "http_request_failed",
                method=scope.get("method", ""),
                path=scope.get("path", ""),
                route=_route_path(scope),
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            raise
        finally:
            reset_request_id(token)


class BodyLimitMiddleware:
    """Rejeita corpos acima de `MAX_REQUEST_BODY_BYTES` com `413` padronizado."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.logger = get_logger("nexa.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None:
            if content_length > self.max_bytes:
                await self._reject(scope, send, content_length)
                return
            await self.app(scope, receive, send)
            return

        # Sem Content-Length (chunked): acumula até o limite e reproduz o corpo.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await self._reject(scope, send, len(body))
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    def _content_length(self, scope: Scope) -> int | None:
        raw = _header(scope, b"content-length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value >= 0 else None

    async def _reject(self, scope: Scope, send: Send, size: int) -> None:
        self.logger.warning(
            "payload_too_large",
            path=scope.get("path", ""),
            content_length=size,
            limit=self.max_bytes,
        )
        start, end = send_payload_too_large(scope)
        await send(start)
        await send(end)


class ContentTypeMiddleware:
    """Exige `application/json` em corpos não vazios (RNF-SEG-09, 415)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = get_logger("nexa.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        if not _has_body(scope):
            await self.app(scope, receive, send)
            return

        content_type = _header(scope, b"content-type")
        if content_type is None or not _is_json_media_type(content_type):
            self.logger.warning(
                "unsupported_media_type",
                path=scope.get("path", ""),
                content_type=content_type or "",
            )
            start, end = send_unsupported_media_type(scope)
            await send(start)
            await send(end)
            return

        await self.app(scope, receive, send)


def _is_json_media_type(value: str) -> bool:
    """Aceita `application/json` e sufixos `+json`, com parâmetros opcionais."""
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _has_body(scope: Scope) -> bool:
    """Há corpo declarado? Requisições sem corpo (ex.: `POST /auth/guest`) passam."""
    raw_length = _header(scope, b"content-length")
    if raw_length is not None:
        try:
            if int(raw_length) > 0:
                return True
        except ValueError:
            return True
    transfer_encoding = _header(scope, b"transfer-encoding")
    return bool(transfer_encoding and transfer_encoding.strip())


__all__ = ["BodyLimitMiddleware", "ContentTypeMiddleware", "RequestContextMiddleware"]

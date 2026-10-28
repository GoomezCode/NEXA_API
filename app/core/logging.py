"""Logs estruturados em JSON com redaction de dados sensíveis (RNF-OBS-01/02)."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any, cast

import structlog
from structlog.types import EventDict, FilteringBoundLogger

_request_id: ContextVar[str] = ContextVar("request_id", default="")

# Chaves cujo valor nunca pode aparecer em log (comparação normalizada).
_SENSITIVE_KEYS = frozenset(
    {
        "admin_password_hash",
        "api_key",
        "apikey",
        "authorization",
        "cloud_api_key",
        "content",
        "jwt_secret",
        "messages",
        "password",
        "passwd",
        "prompt",
        "refresh_token",
        "secret",
        "token",
        "x_api_key",
    }
)

_REDACTED = "***"


def current_request_id() -> str:
    """`request_id` da requisição em andamento (vazio fora de uma)."""
    return _request_id.get()


def bind_request_id(value: str) -> Token[str]:
    """Associa o `request_id` ao contexto atual."""
    return _request_id.set(value)


def reset_request_id(token: Token[str]) -> None:
    """Desfaz `bind_request_id`."""
    _request_id.reset(token)


def _normalize_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_")


def redact_sensitive(value: Any) -> Any:
    """Mascara recursivamente chaves sensíveis em dicionários e listas."""
    if isinstance(value, dict):
        return {
            key: _REDACTED if _normalize_key(key) in _SENSITIVE_KEYS else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    return value


def add_request_id(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Acrescenta o `request_id` corrente a cada evento."""
    del logger, method_name
    request_id = current_request_id()
    if request_id:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def redact_processor(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Processor do structlog que aplica a redaction."""
    del logger, method_name
    redacted = redact_sensitive(event_dict)
    assert isinstance(redacted, dict)
    return cast(EventDict, redacted)


def configure_logging(level: str = "INFO") -> None:
    """Configura structlog e o logging da stdlib para JSON."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, format="%(message)s", force=True)
    structlog.configure(
        processors=[
            add_request_id,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Logger estruturado com a configuração global."""
    return cast(FilteringBoundLogger, structlog.get_logger(name))


__all__ = [
    "add_request_id",
    "bind_request_id",
    "configure_logging",
    "current_request_id",
    "get_logger",
    "redact_processor",
    "redact_sensitive",
    "reset_request_id",
]

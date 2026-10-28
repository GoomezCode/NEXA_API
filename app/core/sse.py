"""Formatação de Server-Sent Events (SRS §3.1.1 e §3.7.3)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

SSE_MEDIA_TYPE = "text/event-stream"

#: Heartbeat enviado após 15 s sem eventos (RF-CHAT-03).
HEARTBEAT_INTERVAL_SECONDS = 15.0

#: Linha de comentário SSE usada como heartbeat.
HEARTBEAT = ": ping\n\n"

#: Cabeçalhos obrigatórios do streaming (SRS §3.1.1).
SSE_HEADERS: dict[str, str] = {
    "Content-Type": SSE_MEDIA_TYPE,
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def format_data(payload: Mapping[str, Any]) -> str:
    """`data:` com o JSON em uma linha (texto com `\\n` não quebra o protocolo)."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {body}\n\n"


def format_event(name: str, payload: Mapping[str, Any]) -> str:
    """Evento nomeado (`event: done` ou `event: error`)."""
    return f"event: {name}\n{format_data(payload)}"


__all__ = [
    "HEARTBEAT",
    "HEARTBEAT_INTERVAL_SECONDS",
    "SSE_HEADERS",
    "SSE_MEDIA_TYPE",
    "format_data",
    "format_event",
]

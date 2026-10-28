"""Fixtures compartilhadas dos testes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.fake import FakeProvider
from app.config import Settings
from app.main import create_app

# Hash argon2id da senha "admin" (apenas para testes).
PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$KMwCxpCk5TYd4cdcRrnSFA$"
    "QKtV/j4myVFG6IfaM7tR6yZnx4KqRu4Q+PtQA958h48"
)

BASE_SETTINGS: dict[str, object] = {
    "_env_file": None,
    "environment": "development",
    "admin_username": "admin",
    "admin_password_hash": PASSWORD_HASH,
    "jwt_secret": "x" * 40,
}


def make_settings(**overrides: object) -> Settings:
    """Settings isolado do ambiente e do `.env`."""
    return Settings(**{**BASE_SETTINGS, **overrides})


class FakeClock:
    """Relógio controlável para testes de expiração e janelas."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    def set(self, now: datetime) -> None:
        self._now = now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def provider() -> FakeProvider:
    """Provedor determinístico, sem acesso à rede."""
    return FakeProvider()


@pytest.fixture
def app(settings: Settings, clock: FakeClock, provider: FakeProvider) -> FastAPI:
    return create_app(settings, clock=clock, provider=provider)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """Evento SSE já interpretado (`event`, `data` e comentário/heartbeat)."""

    event: str | None
    data: dict[str, Any] | None
    comment: str | None


def parse_sse(payload: str) -> list[SSEEvent]:
    """Interpreta o corpo `text/event-stream` em uma lista de eventos."""
    events: list[SSEEvent] = []
    for block in payload.split("\n\n"):
        if not block.strip():
            continue
        name: str | None = None
        comment: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith(":"):
                comment = line[1:].strip()
            elif line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        data = json.loads("\n".join(data_lines)) if data_lines else None
        events.append(SSEEvent(event=name, data=data, comment=comment))
    return events

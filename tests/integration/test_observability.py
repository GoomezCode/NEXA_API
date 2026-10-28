"""Testes de observabilidade: logs JSON, correlação e eventos (F8, RNF-OBS-01..05)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.base import ProviderError
from app.ai.fake import FakeProvider
from app.config import Settings
from app.main import create_app
from tests.conftest import FakeClock, make_settings

DEFAULT_MESSAGES = [{"role": "user", "content": "Olá"}]


def log_events(payload: str, event: str | None = None) -> list[dict[str, Any]]:
    """Extrai os eventos JSON de stdout/stderr, opcionalmente filtrando por nome."""
    events: list[dict[str, Any]] = []
    for line in payload.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and (event is None or parsed.get("event") == event):
            events.append(parsed)
    return events


def login(client: TestClient, password: str = "admin") -> Any:
    return client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": password},
    )


def guest_token(client: TestClient) -> str:
    token: str = client.post("/api/v1/auth/guest").json()["access_token"]
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def chat(client: TestClient, token: str, *, stream: bool = False) -> Any:
    return client.post(
        "/api/v1/chat",
        headers=auth(token),
        json={"messages": DEFAULT_MESSAGES, "stream": stream},
    )


@contextmanager
def client_for(settings: Settings, clock: FakeClock) -> Iterator[tuple[TestClient, FakeProvider]]:
    provider = FakeProvider()
    with TestClient(create_app(settings, clock=clock, provider=provider)) as client:
        yield client, provider


def test_http_request_log_has_required_fields(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    request_id = str(uuid.uuid4())
    capsys.readouterr()

    response = client.get("/health", headers={"X-Request-ID": request_id})

    events = log_events(capsys.readouterr().out, "http_request")
    assert response.status_code == 200
    assert len(events) == 1
    event = events[0]
    assert event["level"] == "info"
    assert event["timestamp"]
    assert event["request_id"] == request_id
    assert event["method"] == "GET"
    assert event["path"] == "/health"
    assert event["route"] == "/health"
    assert event["status"] == 200
    assert event["duration_ms"] >= 0


def test_chat_log_has_role_provider_model_duration_and_tokens(
    client: TestClient,
    provider: FakeProvider,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = guest_token(client)
    capsys.readouterr()

    response = chat(client, token)
    request_id = response.headers["x-request-id"]
    captured = capsys.readouterr().out

    completed = log_events(captured, "chat_completed")
    assert len(completed) == 1
    event = completed[0]
    assert event["request_id"] == request_id
    assert event["role"] == "guest"
    assert event["provider"] == "fake"
    assert event["model"] == "fake-model"
    assert event["finish_reason"] == "stop"
    assert event["duration_ms"] >= 0
    assert event["prompt_tokens"] == 11
    assert event["completion_tokens"] == 7

    # RNF-OBS-05: o mesmo identificador chega ao provedor e aparece no acesso.
    assert provider.calls[0].request_id == request_id
    access = log_events(captured, "http_request")
    assert access and access[0]["request_id"] == request_id
    assert access[0]["route"] == "/api/v1/chat"


def test_stream_log_has_metrics_and_request_id(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    token = guest_token(client)
    capsys.readouterr()

    response = chat(client, token, stream=True)
    request_id = response.headers["x-request-id"]
    captured = capsys.readouterr().out

    completed = log_events(captured, "chat_stream_completed")
    assert len(completed) == 1
    event = completed[0]
    assert event["request_id"] == request_id
    assert event["role"] == "guest"
    assert event["provider"] == "fake"
    assert event["model"] == "fake-model"
    assert event["finish_reason"] == "stop"
    assert event["duration_ms"] >= 0
    assert event["prompt_tokens"] == 11
    assert event["completion_tokens"] == 7


def test_failed_login_and_lockout_are_logged(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    for _ in range(5):
        assert login(client, password="senha-errada-123").status_code == 401
    locked = login(client, password="senha-errada-123")
    captured = capsys.readouterr()

    failures = log_events(captured.out, "admin_login_failed")
    lockouts = log_events(captured.out, "admin_login_locked")
    assert locked.status_code == 429
    assert len(failures) == 5
    assert failures[0]["level"] == "info"
    assert len(lockouts) == 1
    assert lockouts[0]["level"] == "warning"
    assert lockouts[0]["retry_after"] == 900
    assert "senha-errada-123" not in captured.out + captured.err


def test_usage_limit_is_logged(clock: FakeClock, capsys: pytest.CaptureFixture[str]) -> None:
    settings = make_settings(guest_daily_messages=1)
    with client_for(settings, clock) as (client, _):
        token = guest_token(client)
        assert chat(client, token).status_code == 200
        capsys.readouterr()

        blocked = chat(client, token)

    captured = capsys.readouterr().out
    events = log_events(captured, "chat_rate_limited")
    assert blocked.status_code == 429
    assert len(events) == 1
    event = events[0]
    assert event["level"] == "warning"
    assert event["role"] == "guest"
    assert event["code"] == "guest_daily_limit_reached"
    assert event["retry_after"] > 0
    assert event["request_id"] == blocked.headers["x-request-id"]


def test_provider_errors_are_logged_without_content(
    client: TestClient,
    provider: FakeProvider,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = guest_token(client)
    secret = "SEGREDO-NAO-PODE-VAZAR-789"
    provider.error = ProviderError(f"falha ao processar: {secret}")
    capsys.readouterr()

    response = client.post(
        "/api/v1/chat",
        headers=auth(token),
        json={"messages": [{"role": "user", "content": secret}], "stream": False},
    )

    captured = capsys.readouterr()
    events = log_events(captured.out, "chat_provider_failed")
    assert response.status_code == 502
    assert len(events) == 1
    event = events[0]
    assert event["level"] == "warning"
    assert event["role"] == "guest"
    assert event["provider"] == "fake"
    assert event["model"] == "fake-model"
    assert event["code"] == "ai_provider_error"
    assert event["request_id"] == response.headers["x-request-id"]
    assert secret not in captured.out + captured.err


def test_stream_provider_error_is_logged(
    client: TestClient,
    provider: FakeProvider,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = guest_token(client)
    provider.error = ProviderError("falha simulada")
    capsys.readouterr()

    response = chat(client, token, stream=True)

    captured = capsys.readouterr()
    events = log_events(captured.out, "chat_stream_failed")
    assert response.status_code == 200
    assert len(events) == 1
    event = events[0]
    assert event["level"] == "warning"
    assert event["role"] == "guest"
    assert event["provider"] == "fake"
    assert event["model"] == "fake-model"
    assert event["code"] == "ai_provider_error"
    assert event["request_id"] == response.headers["x-request-id"]

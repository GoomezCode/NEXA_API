"""Testes de integração de `GET /usage` e da cota na emissão de guest (F3-07)."""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.fake import FakeProvider
from app.config import Settings
from app.core.security import decode_access_token
from app.core.usage_store import InMemoryUsageStore
from app.main import create_app
from tests.conftest import FakeClock, make_settings

TEST_IP = "testclient"


def _guest_token(client: TestClient) -> str:
    token: str = client.post("/api/v1/auth/guest").json()["access_token"]
    return token


def _admin_token(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    token: str = response.json()["access_token"]
    return token


def _store(app: FastAPI) -> InMemoryUsageStore:
    store: InMemoryUsageStore = app.state.usage_store
    return store


def test_usage_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/usage")

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert response.headers["www-authenticate"] == "Bearer"


def test_guest_usage_returns_initial_limits(client: TestClient, settings: Settings) -> None:
    token = _guest_token(client)

    response = client.get("/api/v1/usage", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "role": "guest",
        "daily_limit": settings.guest_daily_messages,
        "daily_remaining": min(settings.guest_daily_messages, settings.guest_ip_daily_messages),
        "per_minute_limit": settings.guest_rate_per_min,
        "per_minute_remaining": settings.guest_rate_per_min,
        "resets_at": "2026-10-29T00:00:00Z",
    }


def test_admin_usage_has_null_daily_fields(client: TestClient, settings: Settings) -> None:
    token = _admin_token(client)

    response = client.get("/api/v1/usage", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "role": "admin",
        "daily_limit": None,
        "daily_remaining": None,
        "per_minute_limit": settings.rate_limit_admin_chat_per_min,
        "per_minute_remaining": settings.rate_limit_admin_chat_per_min,
        "resets_at": None,
    }


async def test_guest_usage_reflects_consumption(
    client: TestClient,
    app: FastAPI,
    settings: Settings,
    clock: FakeClock,
) -> None:
    token = _guest_token(client)
    gid = decode_access_token(token, settings, now=clock.now()).subject

    await _store(app).consume(role="guest", subject=gid, ip=TEST_IP)

    body = client.get("/api/v1/usage", headers={"Authorization": f"Bearer {token}"}).json()

    assert (
        body["daily_remaining"]
        == min(settings.guest_daily_messages, settings.guest_ip_daily_messages) - 1
    )
    assert body["per_minute_remaining"] == settings.guest_rate_per_min - 1


async def test_guest_usage_reflects_ip_quota(clock: FakeClock) -> None:
    settings = make_settings(guest_daily_messages=10, guest_ip_daily_messages=5)
    app = create_app(settings, clock=clock, provider=FakeProvider())

    with TestClient(app) as client:
        token = _guest_token(client)
        # Outro guest, mesmo IP, consome a cota compartilhada.
        await _store(app).consume(role="guest", subject=str(uuid.uuid4()), ip=TEST_IP)
        body = client.get("/api/v1/usage", headers={"Authorization": f"Bearer {token}"}).json()

    assert body["daily_remaining"] == 4
    # O gid consultado continua sem consumo próprio.
    assert body["per_minute_remaining"] == settings.guest_rate_per_min


async def test_admin_usage_reflects_minute_consumption(
    client: TestClient,
    app: FastAPI,
    settings: Settings,
) -> None:
    token = _admin_token(client)

    await _store(app).consume(role="admin", subject="admin", ip=TEST_IP)

    body = client.get("/api/v1/usage", headers={"Authorization": f"Bearer {token}"}).json()

    assert body["per_minute_remaining"] == settings.rate_limit_admin_chat_per_min - 1


async def test_guest_token_usage_reflects_existing_ip_usage(clock: FakeClock) -> None:
    settings = make_settings(guest_daily_messages=10, guest_ip_daily_messages=5)
    app = create_app(settings, clock=clock, provider=FakeProvider())

    with TestClient(app) as client:
        await _store(app).consume(role="guest", subject="outro-gid", ip=TEST_IP)
        response = client.post("/api/v1/auth/guest")

    assert response.status_code == 200
    assert response.json()["usage"]["daily_remaining"] == 4


def test_usage_is_documented(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/api/v1/usage"]

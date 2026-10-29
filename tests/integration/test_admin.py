"""Testes de integração da administração (F6, CA-12/CA-17/CA-20)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.ai.base import ProviderError
from app.ai.fake import FakeProvider
from app.config import Settings
from app.main import create_app
from tests.conftest import FakeClock

ADMIN_PATHS = (
    ("GET", "/api/v1/admin/stats"),
    ("GET", "/api/v1/admin/ai/status"),
    ("GET", "/api/v1/admin/guests"),
)


def _admin_token(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    token: str = response.json()["access_token"]
    return token


def _guest_token(client: TestClient) -> str:
    token: str = client.post("/api/v1/auth/guest").json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _chat(client: TestClient, token: str, *, stream: bool = False) -> object:
    return client.post(
        "/api/v1/chat",
        headers=_auth(token),
        json={"messages": [{"role": "user", "content": "Olá"}], "stream": stream},
    )


@contextmanager
def client_for(settings: Settings, clock: FakeClock) -> Iterator[tuple[TestClient, FakeProvider]]:
    provider = FakeProvider()
    with TestClient(create_app(settings, clock=clock, provider=provider)) as client:
        yield client, provider


@pytest.mark.parametrize(("method", "path"), ADMIN_PATHS)
def test_guest_is_forbidden_on_admin_reads(client: TestClient, method: str, path: str) -> None:
    token = _guest_token(client)

    response = client.request(method, path, headers=_auth(token))

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "forbidden"


def test_guest_is_forbidden_on_admin_reset(client: TestClient) -> None:
    token = _guest_token(client)

    response = client.post(
        "/api/v1/admin/usage/reset",
        headers=_auth(token),
        json={"scope": "gid", "value": "qualquer"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_missing_token_is_unauthorized_on_admin(client: TestClient) -> None:
    response = client.get("/api/v1/admin/stats")

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_stats_start_at_zero(client: TestClient, clock: FakeClock) -> None:
    token = _admin_token(client)

    response = client.get("/api/v1/admin/stats", headers=_auth(token))

    assert response.status_code == 200
    assert response.json() == {
        "since": "2026-10-28T12:00:00Z",
        "chat_requests_total": 0,
        "guest_distinct": 0,
        "rate_limited_total": 0,
        "provider_errors_total": 0,
        "avg_response_seconds": 0.0,
    }


def test_stats_track_chat_rate_limit_and_provider_errors(
    clock: FakeClock, settings: Settings
) -> None:
    with client_for(settings.model_copy(update={"guest_daily_messages": 1}), clock) as (
        client,
        provider,
    ):
        admin = _admin_token(client)
        guest = _guest_token(client)
        assert _chat(client, guest).status_code == 200
        assert _chat(client, guest).status_code == 429

        other = _guest_token(client)
        provider.error = ProviderError("caiu")
        assert _chat(client, other).status_code == 502

        stats = client.get("/api/v1/admin/stats", headers=_auth(admin)).json()

    assert stats["chat_requests_total"] == 3
    assert stats["guest_distinct"] == 2
    assert stats["rate_limited_total"] == 1
    assert stats["provider_errors_total"] == 1
    assert stats["avg_response_seconds"] >= 0.0


def test_stats_count_stream_errors(clock: FakeClock, settings: Settings) -> None:
    with client_for(settings, clock) as (client, provider):
        admin = _admin_token(client)
        guest = _guest_token(client)
        provider.error = ProviderError("caiu no stream")
        assert _chat(client, guest, stream=True).status_code == 200

        stats = client.get("/api/v1/admin/stats", headers=_auth(admin)).json()

    assert stats["chat_requests_total"] == 1
    assert stats["provider_errors_total"] == 1


def test_ai_status_reports_provider_without_secrets(client: TestClient) -> None:
    token = _admin_token(client)

    response = client.get("/api/v1/admin/ai/status", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    # Em v2.0, o status inclui a lista de provedores.
    assert body["providers"][0]["provider"] == "fake"
    assert body["providers"][0]["available"] is True
    assert body["providers"][0]["default_model"] == "fake-model"
    assert body["providers"][0]["checked_at"] == "2026-10-28T12:00:00Z"
    assert body["providers"][0]["error"] is None


@pytest.mark.parametrize("failure", ["unhealthy", "raises"])
def test_ai_status_reports_unavailable(client: TestClient, provider: FakeProvider, failure: str) -> None:
    token = _admin_token(client)
    if failure == "unhealthy":
        provider.healthy = False
    else:
        provider.health_error = ProviderError("sem resposta")

    response = client.get("/api/v1/admin/ai/status", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    # Em v2.0, o campo 'available' está dentro de cada provedor da lista.
    assert body["providers"][0]["available"] is False


def test_guest_listing_exposes_gid_and_usage(client: TestClient) -> None:
    token = _admin_token(client)
    guest = _guest_token(client)
    assert _chat(client, guest).status_code == 200

    response = client.get("/api/v1/admin/guests", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert len(body["guests"]) == 1
    entry = body["guests"][0]
    assert entry["ip"] == "testclient"
    assert entry["daily_used"] == 1
    assert entry["daily_limit"] == 10
    assert entry["last_seen_at"] == "2026-10-28T12:00:00Z"
    assert len(entry["gid"]) == 36
    assert body["ip_usage"] == [{"ip": "testclient", "daily_used": 1, "daily_limit": 30}]


def test_reset_gid_restores_the_guest_quota(client: TestClient) -> None:
    admin = _admin_token(client)
    guest = _guest_token(client)
    assert _chat(client, guest).status_code == 200
    gid = client.get("/api/v1/admin/guests", headers=_auth(admin)).json()["guests"][0]["gid"]

    response = client.post(
        "/api/v1/admin/usage/reset",
        headers=_auth(admin),
        json={"scope": "gid", "value": gid},
    )

    assert response.status_code == 204
    assert response.content == b""
    usage = client.get("/api/v1/usage", headers=_auth(guest)).json()
    assert usage["daily_remaining"] == 10


def test_reset_ip_clears_the_ip_counter(client: TestClient) -> None:
    admin = _admin_token(client)
    guest = _guest_token(client)
    assert _chat(client, guest).status_code == 200

    response = client.post(
        "/api/v1/admin/usage/reset",
        headers=_auth(admin),
        json={"scope": "ip", "value": "testclient"},
    )

    assert response.status_code == 204
    ip_usage = client.get("/api/v1/admin/guests", headers=_auth(admin)).json()["ip_usage"]
    assert ip_usage == [{"ip": "testclient", "daily_used": 0, "daily_limit": 30}]


@pytest.mark.parametrize(
    ("scope", "value"),
    [("gid", "6f1c9a3e-1b2d-4c5f-8a90-0123456789ab"), ("ip", "198.51.100.9")],
)
def test_reset_unknown_target_returns_not_found(client: TestClient, scope: str, value: str) -> None:
    token = _admin_token(client)

    response = client.post(
        "/api/v1/admin/usage/reset",
        headers=_auth(token),
        json={"scope": scope, "value": value},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_reset_rejects_invalid_scope(client: TestClient) -> None:
    token = _admin_token(client)

    response = client.post(
        "/api/v1/admin/usage/reset",
        headers=_auth(token),
        json={"scope": "guest", "value": "x"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["field"] == "scope"


def test_reset_rejects_unknown_fields(client: TestClient) -> None:
    token = _admin_token(client)

    response = client.post(
        "/api/v1/admin/usage/reset",
        headers=_auth(token),
        json={"scope": "gid", "value": "x", "extra": True},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert any(error["field"] == "extra" for error in body["errors"])


def test_admin_endpoints_are_documented_with_examples(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for _, path in ADMIN_PATHS:
        assert path in paths
    assert "/api/v1/admin/usage/reset" in paths

    reset = paths["/api/v1/admin/usage/reset"]["post"]
    assert "204" in reset["responses"]
    assert "404" in reset["responses"]

    schemas = spec["components"]["schemas"]
    assert schemas["StatsResponse"]["example"]["chat_requests_total"] == 128
    assert schemas["ResetRequest"]["example"]["scope"] == "gid"
    assert schemas["GuestsResponse"]["example"]["guests"][0]["gid"]

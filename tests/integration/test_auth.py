"""Testes de integração da autenticação (F1-03/F1-04)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user, require_admin
from app.config import Settings
from app.core.security import create_access_token, decode_access_token
from app.main import create_app
from tests.conftest import FakeClock, make_settings


@contextmanager
def client_for(
    settings: Settings,
    clock: FakeClock,
    *,
    client_address: tuple[str, int] = ("127.0.0.1", 1234),
) -> Iterator[TestClient]:
    with TestClient(create_app(settings, clock=clock), client=client_address) as test_client:
        yield test_client


def login(client: TestClient, password: str = "admin") -> object:
    return client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": password},
    )


def test_login_success_returns_admin_token(
    client: TestClient, settings: Settings, clock: FakeClock
) -> None:
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.admin_token_expire_minutes * 60
    assert body["role"] == "admin"

    payload = decode_access_token(body["access_token"], settings, now=clock.now())
    assert payload.subject == "admin"
    assert payload.role == "admin"


def test_login_rejects_wrong_password(client: TestClient) -> None:
    response = login(client, password="errada")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "invalid_credentials"
    assert body["detail"] == "Usuário ou senha inválidos."
    assert response.headers["www-authenticate"] == "Bearer"


def test_login_rejects_unknown_user_indistinguishably(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "ninguem", "password": "admin"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


@pytest.mark.parametrize(
    "body",
    [
        {"username": "admin", "password": "admin", "extra": True},
        {"username": "admin"},
        {"username": "", "password": "admin"},
        {"username": "admin", "password": ""},
    ],
)
def test_login_validates_body(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/api/v1/auth/login", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_login_locks_after_max_attempts(client: TestClient, clock: FakeClock) -> None:
    for _ in range(5):
        assert login(client, password="errada").status_code == 401

    locked = login(client)
    assert locked.status_code == 429
    body = locked.json()
    assert body["code"] == "login_locked"
    assert locked.headers["retry-after"] == "900"

    clock.advance(900)
    assert login(client).status_code == 200


def test_successful_login_resets_failures(client: TestClient) -> None:
    for _ in range(4):
        assert login(client, password="errada").status_code == 401
    assert login(client).status_code == 200

    for _ in range(4):
        assert login(client, password="errada").status_code == 401
    # Se o contador não tivesse sido zerado, a 5ª falha já teria bloqueado.
    assert login(client).status_code == 200


def test_guest_token_returns_gid_and_usage(
    client: TestClient, settings: Settings, clock: FakeClock
) -> None:
    response = client.post("/api/v1/auth/guest")
    assert response.status_code == 200
    body = response.json()

    assert body["token_type"] == "bearer"
    assert body["role"] == "guest"
    assert body["expires_in"] == settings.guest_token_expire_hours * 3600
    assert body["usage"] == {
        "daily_limit": settings.guest_daily_messages,
        "daily_remaining": min(settings.guest_daily_messages, settings.guest_ip_daily_messages),
        "per_minute_limit": settings.guest_rate_per_min,
        "per_minute_remaining": settings.guest_rate_per_min,
        "resets_at": "2026-10-29T00:00:00Z",
    }

    payload = decode_access_token(body["access_token"], settings, now=clock.now())
    assert payload.role == "guest"
    uuid.UUID(payload.subject)


def test_guest_usage_reflects_most_restrictive_quota(clock: FakeClock) -> None:
    settings = make_settings(
        guest_daily_messages=10,
        guest_ip_daily_messages=4,
        guest_rate_per_min=2,
    )
    with client_for(settings, clock) as client:
        response = client.post("/api/v1/auth/guest")

    usage = response.json()["usage"]
    assert usage["daily_limit"] == 10
    assert usage["daily_remaining"] == 4
    assert usage["per_minute_limit"] == 2
    assert usage["per_minute_remaining"] == 2


def test_guest_token_issuance_is_limited_by_ip(clock: FakeClock) -> None:
    settings = make_settings(guest_tokens_per_ip_per_hour=2)
    with client_for(settings, clock) as client:
        assert client.post("/api/v1/auth/guest").status_code == 200
        assert client.post("/api/v1/auth/guest").status_code == 200

        blocked = client.post("/api/v1/auth/guest")
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "guest_token_limit_reached"
        assert blocked.headers["retry-after"] == "3600"

        clock.advance(3600)
        assert client.post("/api/v1/auth/guest").status_code == 200


def test_guest_token_limit_uses_forwarded_ip_only_from_trusted_proxy(
    clock: FakeClock,
) -> None:
    settings = make_settings(
        guest_tokens_per_ip_per_hour=1,
        trusted_proxies="127.0.0.1/32",
    )
    with client_for(settings, clock) as client:
        first = client.post("/api/v1/auth/guest", headers={"X-Forwarded-For": "203.0.113.10"})
        same_ip = client.post("/api/v1/auth/guest", headers={"X-Forwarded-For": "203.0.113.10"})
        other_ip = client.post("/api/v1/auth/guest", headers={"X-Forwarded-For": "203.0.113.11"})

    assert first.status_code == 200
    assert same_ip.status_code == 429
    assert other_ip.status_code == 200


def test_forwarded_ip_is_ignored_when_proxy_is_not_trusted(clock: FakeClock) -> None:
    settings = make_settings(guest_tokens_per_ip_per_hour=1, trusted_proxies="")
    with client_for(settings, clock) as client:
        assert (
            client.post(
                "/api/v1/auth/guest", headers={"X-Forwarded-For": "203.0.113.10"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/auth/guest", headers={"X-Forwarded-For": "203.0.113.11"}
            ).status_code
            == 429
        )


# --- Rotas protegidas (dependências) ---------------------------------------


def _add_protected_routes(app: FastAPI) -> None:
    @app.get("/_test/me")
    async def me(user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
        return {"sub": user.subject, "role": user.role}

    @app.get("/_test/admin")
    async def admin(user: CurrentUser = Depends(require_admin)) -> dict[str, str]:
        return {"sub": user.subject, "role": user.role}


def _admin_token(client: TestClient) -> str:
    token: str = login(client).json()["access_token"]
    return token


def test_protected_route_requires_token(client: TestClient, app: FastAPI) -> None:
    _add_protected_routes(app)
    response = client.get("/_test/me")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    ["Basic abc", "Bearer", "Bearer ", "Token abc", "Bearer invalido"],
)
def test_protected_route_rejects_malformed_tokens(
    client: TestClient, app: FastAPI, header: str
) -> None:
    _add_protected_routes(app)
    response = client.get("/_test/me", headers={"Authorization": header})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_expired_token_is_rejected(
    client: TestClient, app: FastAPI, settings: Settings, clock: FakeClock
) -> None:
    _add_protected_routes(app)
    expired = create_access_token(
        settings, subject="admin", role="admin", expires_in=-10, now=clock.now()
    )
    response = client.get("/_test/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


def test_token_with_unknown_role_is_rejected(
    client: TestClient, app: FastAPI, settings: Settings, clock: FakeClock
) -> None:
    _add_protected_routes(app)
    now = int(clock.now().timestamp())
    token = jwt.encode(
        {"sub": "x", "role": "root", "iat": now, "exp": now + 600},
        settings.jwt_secret_value,
        algorithm=settings.jwt_algorithm,
    )
    response = client.get("/_test/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_guest_can_access_guest_route_but_not_admin(client: TestClient, app: FastAPI) -> None:
    _add_protected_routes(app)
    guest_token = client.post("/api/v1/auth/guest").json()["access_token"]
    headers = {"Authorization": f"Bearer {guest_token}"}

    me = client.get("/_test/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "guest"
    uuid.UUID(me.json()["sub"])

    forbidden = client.get("/_test/admin", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"


def test_admin_can_access_admin_route(client: TestClient, app: FastAPI) -> None:
    _add_protected_routes(app)
    headers = {"Authorization": f"Bearer {_admin_token(client)}"}

    me = client.get("/_test/me", headers=headers)
    assert me.status_code == 200
    assert me.json() == {"sub": "admin", "role": "admin"}

    admin = client.get("/_test/admin", headers=headers)
    assert admin.status_code == 200
    assert admin.json()["role"] == "admin"


def test_openapi_lists_auth_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/guest" in paths
    assert "post" in paths["/api/v1/auth/login"]
    assert "post" in paths["/api/v1/auth/guest"]


def test_login_is_not_cached(client: TestClient) -> None:
    response = login(client, password="errada")
    assert response.headers.get("cache-control") == "no-store"

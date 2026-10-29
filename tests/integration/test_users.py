"""Endpoints de usuários, fallback do admin e modo degradado do SQLite (F13)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.fake import FakeProvider
from app.config import Settings
from app.db.models import ADMIN_ID
from app.main import create_app
from tests.conftest import FakeClock, make_settings

PASSWORD = "senha-forte-123"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, username: str, password: str) -> object:
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


def _admin_token(client: TestClient) -> str:
    response = _login(client, "admin", "admin")
    assert response.status_code == 200
    token: str = response.json()["access_token"]
    return token


def _guest_token(client: TestClient) -> str:
    token: str = client.post("/api/v1/auth/guest").json()["access_token"]
    return token


def _create(client: TestClient, token: str, **payload: object) -> dict[str, object]:
    body = {"username": "teste01", "password": PASSWORD, **payload}
    response = client.post("/api/v1/admin/users", json=body, headers=_auth(token))
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()
    return data


def _chat(client: TestClient, token: str) -> object:
    return client.post(
        "/api/v1/chat",
        headers=_auth(token),
        json={"messages": [{"role": "user", "content": "Olá"}], "stream": False},
    )


@contextmanager
def client_for(settings: Settings, clock: FakeClock) -> Iterator[TestClient]:
    app = create_app(settings, clock=clock, provider=FakeProvider())
    with TestClient(app) as client:
        yield client


def test_full_test_user_lifecycle(client: TestClient) -> None:
    admin = _admin_token(client)
    created = _create(
        client,
        admin,
        email="teste01@example.com",
        daily_message_limit=2,
        rate_per_min=30,
    )
    user_id = str(created["id"])

    assert created["password"] == PASSWORD
    assert created["username"] == "teste01"
    assert created["role"] == "user"
    assert created["is_test"] is True
    assert created["is_protected"] is False
    assert "password_hash" not in created

    login = _login(client, "teste01", PASSWORD)
    assert login.status_code == 200
    body = login.json()
    assert body["role"] == "user"
    assert body["user_id"] == user_id
    user_token = body["access_token"]

    usage = client.get("/api/v1/usage", headers=_auth(user_token))
    assert usage.status_code == 200
    assert usage.json()["daily_limit"] == 2

    listing = client.get("/api/v1/admin/users?role=user", headers=_auth(admin))
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["users"]] == [user_id]

    fetched = client.get(f"/api/v1/admin/users/{user_id}", headers=_auth(admin))
    assert fetched.status_code == 200
    assert fetched.json()["email"] == "teste01@example.com"

    updated = client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"daily_message_limit": 5, "is_active": False},
        headers=_auth(admin),
    )
    assert updated.status_code == 200
    assert updated.json()["daily_message_limit"] == 5
    assert updated.json()["is_active"] is False

    blocked = client.get("/api/v1/usage", headers=_auth(user_token))
    assert blocked.status_code == 401
    assert blocked.json()["code"] == "invalid_token"

    assert (
        client.patch(
            f"/api/v1/admin/users/{user_id}",
            json={"is_active": True},
            headers=_auth(admin),
        ).status_code
        == 200
    )
    assert _login(client, "teste01", PASSWORD).status_code == 200

    reset = client.post(
        f"/api/v1/admin/users/{user_id}/reset-password",
        json={"password": "outra-senha-123"},
        headers=_auth(admin),
    )
    assert reset.status_code == 200
    assert reset.json()["password"] == "outra-senha-123"
    assert reset.headers["cache-control"] == "no-store"
    assert _login(client, "teste01", PASSWORD).status_code == 401
    assert _login(client, "teste01", "outra-senha-123").status_code == 200

    deleted = client.delete(f"/api/v1/admin/users/{user_id}", headers=_auth(admin))
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/admin/users/{user_id}", headers=_auth(admin)).status_code == 404


def test_bulk_creates_sequential_users(client: TestClient, settings: Settings) -> None:
    admin = _admin_token(client)

    first = client.post(
        "/api/v1/admin/users/bulk",
        json={"prefix": "lote", "count": 3},
        headers=_auth(admin),
    )
    assert first.status_code == 201
    users = first.json()["users"]
    assert [item["username"] for item in users] == ["lote01", "lote02", "lote03"]
    assert all(item["password"] for item in users)
    assert _login(client, "lote01", users[0]["password"]).status_code == 200

    second = client.post(
        "/api/v1/admin/users/bulk",
        json={"prefix": "lote", "count": 2},
        headers=_auth(admin),
    )
    assert second.status_code == 201
    assert [item["username"] for item in second.json()["users"]] == ["lote04", "lote05"]

    too_many = client.post(
        "/api/v1/admin/users/bulk",
        json={"prefix": "limite", "count": settings.test_user_max_bulk + 1},
        headers=_auth(admin),
    )
    assert too_many.status_code == 422
    assert too_many.json()["code"] == "validation_error"


def test_admin_is_protected_on_every_write_route(client: TestClient) -> None:
    admin = _admin_token(client)

    for method, path, payload in (
        ("patch", f"/api/v1/admin/users/{ADMIN_ID}", {"is_active": False}),
        ("post", f"/api/v1/admin/users/{ADMIN_ID}/reset-password", {"password": "outra-123"}),
        ("delete", f"/api/v1/admin/users/{ADMIN_ID}", None),
    ):
        response = client.request(
            method,
            path,
            json=payload,
            headers=_auth(admin),
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "admin_protected"

    assert client.get(f"/api/v1/admin/users/{ADMIN_ID}", headers=_auth(admin)).status_code == 200


@pytest.mark.parametrize("kind", ["guest", "user"])
def test_non_admin_roles_cannot_manage_users(client: TestClient, kind: str) -> None:
    admin = _admin_token(client)
    if kind == "guest":
        token = _guest_token(client)
    else:
        _create(client, admin, username="comum")
        token = _login(client, "comum", PASSWORD).json()["access_token"]

    response = client.get("/api/v1/admin/users", headers=_auth(token))

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_duplicate_username_or_email_is_a_conflict(client: TestClient) -> None:
    admin = _admin_token(client)
    _create(client, admin, username="teste01", email="teste01@example.com")

    duplicate_username = client.post(
        "/api/v1/admin/users",
        json={"username": "teste01", "password": PASSWORD},
        headers=_auth(admin),
    )
    assert duplicate_username.status_code == 409
    assert duplicate_username.json()["code"] == "username_conflict"

    duplicate_email = client.post(
        "/api/v1/admin/users",
        json={"username": "teste02", "password": PASSWORD, "email": "teste01@example.com"},
        headers=_auth(admin),
    )
    assert duplicate_email.status_code == 409


def test_cleanup_removes_expired_users_but_never_the_admin(client: TestClient) -> None:
    admin = _admin_token(client)
    created = _create(
        client,
        admin,
        username="expirado",
        expires_at="2020-01-01T00:00:00Z",
    )
    assert _login(client, "expirado", PASSWORD).status_code == 401

    cleanup = client.post("/api/v1/admin/users/cleanup", headers=_auth(admin))

    assert cleanup.status_code == 200
    assert cleanup.json()["removed"] == 1
    assert (
        client.get(f"/api/v1/admin/users/{created['id']}", headers=_auth(admin)).status_code == 404
    )
    assert client.get(f"/api/v1/admin/users/{ADMIN_ID}", headers=_auth(admin)).status_code == 200


def test_invalid_cursor_is_a_validation_error(client: TestClient) -> None:
    admin = _admin_token(client)

    response = client.get("/api/v1/admin/users?cursor=***", headers=_auth(admin))

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_database_status_and_health(client: TestClient) -> None:
    admin = _admin_token(client)
    _create(client, admin)

    status = client.get("/api/v1/admin/db/status", headers=_auth(admin))
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "ok"
    assert body["wal"] is True
    assert body["migration_version"] == 2
    assert body["users_total"] == 2
    assert body["test_users_total"] == 1
    assert body["size_bytes"] > 0
    assert body["read_latency_ms_p95"] >= 0
    assert body["write_latency_ms_p95"] >= 0

    health = client.get("/health/db")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["wal"] is True

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


def test_user_quota_is_applied_from_the_database(client: TestClient) -> None:
    admin = _admin_token(client)
    _create(client, admin, username="cotado", daily_message_limit=1, rate_per_min=100)
    token = _login(client, "cotado", PASSWORD).json()["access_token"]

    assert _chat(client, token).status_code == 200
    blocked = _chat(client, token)

    assert blocked.status_code == 429
    assert blocked.json()["code"] == "user_daily_limit_reached"


def test_admin_login_survives_database_removal(
    settings: Settings,
    clock: FakeClock,
) -> None:
    with client_for(settings, clock) as client:
        assert _admin_token(client)
        database_path = settings.database_path
        for suffix in ("", "-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)

        assert _login(client, "admin", "admin").status_code == 200


def test_degraded_mode_keeps_admin_login_and_reports_unavailable(
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    database_directory = tmp_path / "diretorio-no-lugar-do-arquivo"
    database_directory.mkdir()
    settings = make_settings(database_url=f"sqlite:///{database_directory}")

    with client_for(settings, clock) as client:
        assert client.get("/health").status_code == 200
        assert _login(client, "admin", "admin").status_code == 200

        user_login = _login(client, "qualquer", "senha")
        assert user_login.status_code == 503
        assert user_login.json()["code"] == "database_unavailable"

        assert client.get("/health/db").status_code == 503
        assert client.get("/health/ready").status_code == 503


def test_test_users_can_be_disabled(clock: FakeClock) -> None:
    settings = make_settings(allow_test_users=False)

    with client_for(settings, clock) as client:
        admin = _admin_token(client)

        response = client.post(
            "/api/v1/admin/users",
            json={"username": "teste01", "password": PASSWORD},
            headers=_auth(admin),
        )

        assert response.status_code == 403
        assert response.json()["code"] == "test_users_disabled"


def test_database_has_no_chat_tables(settings: Settings, client: TestClient) -> None:
    _admin_token(client)
    _create(client, _admin_token(client))

    connection = sqlite3.connect(settings.database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        connection.close()

    assert {row[0] for row in rows} == {"users", "schema_migrations"}

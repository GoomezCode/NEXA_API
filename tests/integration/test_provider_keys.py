"""Isolamento, máscara e criptografia das chaves BYOK."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeClock, make_settings


@contextmanager
def client_for(settings: Settings, clock: FakeClock) -> Iterator[TestClient]:
    with TestClient(create_app(settings, clock=clock), client=("127.0.0.1", 1234)) as client:
        yield client


def register(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "senha-segura-123"},
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def test_provider_key_is_encrypted_masked_and_isolated(clock: FakeClock) -> None:
    settings = make_settings(
        allow_self_registration=True,
        key_encryption_secret="k" * 48,
    )
    with client_for(settings, clock) as client:
        first_token = register(client, "marina")
        second_token = register(client, "joana")
        first_headers = {"Authorization": f"Bearer {first_token}"}
        second_headers = {"Authorization": f"Bearer {second_token}"}

        saved = client.put(
            "/api/v1/me/providers/openai",
            headers=first_headers,
            json={"api_key": "sk-private-key-1234", "default_model": "gpt-4o-mini"},
        )
        first_list = client.get("/api/v1/me/providers", headers=first_headers)
        second_list = client.get("/api/v1/me/providers", headers=second_headers)
    with sqlite3.connect(settings.database_path) as database:
        row = database.execute(
            "SELECT api_key_encrypted FROM user_provider_keys WHERE provider='openai'"
        ).fetchone()

    assert saved.status_code == 200
    assert saved.json()["key_last4"] == "1234"
    assert "api_key" not in saved.json()
    assert first_list.json()[0]["provider"] == "openai"
    assert second_list.json() == []
    assert row is not None and "sk-private-key-1234" not in row[0]


def test_guests_cannot_manage_provider_keys(clock: FakeClock) -> None:
    with client_for(make_settings(key_encryption_secret="k" * 48), clock) as client:
        guest = client.post("/api/v1/auth/guest")
        token = guest.json()["access_token"]
        response = client.get(
            "/api/v1/me/providers", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


def test_provider_key_endpoints_are_unavailable_without_encryption_secret(clock: FakeClock) -> None:
    settings = make_settings(allow_self_registration=True)
    with client_for(settings, clock) as client:
        token = register(client, "marina")
        response = client.get("/api/v1/me/providers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    assert response.json()["code"] == "provider_keys_unavailable"

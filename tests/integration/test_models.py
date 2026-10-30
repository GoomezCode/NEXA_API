"""Testes de integração do catálogo de modelos (RF-CHAT-13/F11)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.ai.base import ModelInfo, ProviderError, ProviderTimeout, ProviderUnavailable
from app.ai.fake import FakeProvider
from app.config import Settings
from app.main import create_app
from tests.conftest import FakeClock, make_settings

MODELS_PATH = "/api/v1/models"


@contextmanager
def client_for(
    settings: Settings,
    clock: FakeClock,
    provider: FakeProvider | None = None,
) -> Iterator[tuple[TestClient, FakeProvider]]:
    fake = provider or FakeProvider()
    with TestClient(create_app(settings, clock=clock, provider=fake)) as client:
        yield client, fake


def _guest_token(client: TestClient) -> str:
    token: str = client.post("/api/v1/auth/guest").json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_models_requires_authentication(client: TestClient) -> None:
    response = client.get(MODELS_PATH)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_guest_lists_installed_models(client: TestClient, provider: FakeProvider) -> None:
    token = _guest_token(client)

    response = client.get(MODELS_PATH, headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    # Em v2.0, o modelo vem na lista providers[0].provider e providers[0].default_model.
    assert body["providers"][0]["provider"] == "fake"
    assert body["providers"][0]["default_model"] == "fake-model:latest"
    assert [model["name"] for model in body["models"]] == [
        "fake-model:latest",
        "fake-small:latest",
    ]
    # O modelo índice tem model_id, provider (no nível raiz), name e selectable.
    assert body["models"][0] == {
        "model_id": "fake/fake-model",
        "provider": "fake",
        "name": "fake-model:latest",
        "selectable": True,
    }
    assert body["models"][1]["selectable"] is True
    assert provider.models_calls == [response.headers["x-request-id"]]


def test_admin_lists_models(client: TestClient) -> None:
    token = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    ).json()["access_token"]

    response = client.get(MODELS_PATH, headers=_auth(token))

    assert response.status_code == 200
    assert len(response.json()["models"]) == 2


def test_allowlist_keeps_listed_and_default_models_selectable(clock: FakeClock) -> None:
    settings = make_settings(ai_allowed_models=["fake-small"])
    with client_for(settings, clock) as (client, _):
        token = _guest_token(client)

        body = client.get(MODELS_PATH, headers=_auth(token)).json()

    selectable = {model["name"]: model["selectable"] for model in body["models"]}
    assert selectable == {
        "fake-model:latest": True,
        "fake-small:latest": True,
    }


def test_allowlist_hides_other_models_from_selection(clock: FakeClock) -> None:
    settings = make_settings(ai_allowed_models=["nao-instalado"])
    with client_for(settings, clock) as (client, _):
        token = _guest_token(client)

        body = client.get(MODELS_PATH, headers=_auth(token)).json()

    selectable = {model["name"]: model["selectable"] for model in body["models"]}
    assert selectable == {
        "fake-model:latest": True,
        "fake-small:latest": False,
    }


def test_catalog_is_cached_between_requests(client: TestClient, provider: FakeProvider) -> None:
    token = _guest_token(client)

    first = client.get(MODELS_PATH, headers=_auth(token))
    second = client.get(MODELS_PATH, headers=_auth(token))

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(provider.models_calls) == 1


def test_refresh_bypasses_the_cache(client: TestClient, provider: FakeProvider) -> None:
    token = _guest_token(client)

    client.get(MODELS_PATH, headers=_auth(token))
    response = client.get(f"{MODELS_PATH}?refresh=true", headers=_auth(token))

    assert response.status_code == 200
    assert len(provider.models_calls) == 2


def test_zero_cache_consults_provider_every_time(clock: FakeClock) -> None:
    settings = make_settings(models_cache_seconds=0)
    with client_for(settings, clock) as (client, provider):
        token = _guest_token(client)

        client.get(MODELS_PATH, headers=_auth(token))
        client.get(MODELS_PATH, headers=_auth(token))

    assert len(provider.models_calls) == 2


def test_cache_expires_after_ttl(clock: FakeClock) -> None:
    settings = make_settings(models_cache_seconds=5)
    with client_for(settings, clock) as (client, provider):
        token = _guest_token(client)

        client.get(MODELS_PATH, headers=_auth(token))
        clock.advance(6)
        client.get(MODELS_PATH, headers=_auth(token))

    assert len(provider.models_calls) == 2


def test_provider_unavailable_returns_503(client: TestClient, provider: FakeProvider) -> None:
    token = _guest_token(client)
    provider.models_error = ProviderUnavailable("fora do ar")

    response = client.get(MODELS_PATH, headers=_auth(token))

    assert response.status_code == 503
    assert response.json()["code"] == "provider_unavailable"


def test_provider_timeout_returns_504(client: TestClient, provider: FakeProvider) -> None:
    token = _guest_token(client)
    provider.models_error = ProviderTimeout("lento")

    response = client.get(MODELS_PATH, headers=_auth(token))

    assert response.status_code == 504
    assert response.json()["code"] == "ai_provider_timeout"


def test_provider_error_returns_502(client: TestClient, provider: FakeProvider) -> None:
    token = _guest_token(client)
    provider.models_error = ProviderError("falhou")

    response = client.get(MODELS_PATH, headers=_auth(token))

    assert response.status_code == 502
    assert response.json()["code"] == "ai_provider_error"


def test_model_without_metadata_serializes_nulls(
    client: TestClient, provider: FakeProvider
) -> None:
    token = _guest_token(client)
    provider.models = (ModelInfo(name="sem-metadados:latest"),)

    response = client.get(MODELS_PATH, headers=_auth(token))

    assert response.status_code == 200
    assert response.json()["models"] == [
        {
            "model_id": "fake/sem-metadados",
            "provider": "fake",
            "name": "sem-metadados:latest",
            "selectable": True,
        }
    ]

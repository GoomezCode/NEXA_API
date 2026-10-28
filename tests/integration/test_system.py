"""Testes de integração dos endpoints de sistema e middlewares (F0-06/07)."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app import __version__
from app.main import create_app
from tests.conftest import make_settings


def _assert_uuid(value: str) -> None:
    uuid.UUID(value)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    _assert_uuid(body["request_id"])
    assert response.headers["x-request-id"] == body["request_id"]


def test_health_accepts_client_request_id(client: TestClient) -> None:
    request_id = str(uuid.uuid4())
    response = client.get("/health", headers={"X-Request-ID": request_id})
    assert response.headers["x-request-id"] == request_id
    assert response.json()["request_id"] == request_id


def test_health_replaces_invalid_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "invalid-uuid"})
    generated = response.headers["x-request-id"]
    assert generated != "invalid-uuid"
    _assert_uuid(generated)


def test_security_headers(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in response.headers


def test_readiness_without_provider_returns_ok(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["provider"] == "ollama"
    _assert_uuid(body["request_id"])


def test_readiness_with_healthy_provider(client: TestClient, app: FastAPI) -> None:
    async def check() -> bool:
        return True

    app.state.ai_health = check
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_with_unhealthy_provider(client: TestClient, app: FastAPI) -> None:
    async def check() -> bool:
        return False

    app.state.ai_health = check
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["provider"] == "ollama"
    assert response.headers["x-request-id"] == body["request_id"]


def test_readiness_with_provider_exception(client: TestClient, app: FastAPI) -> None:
    async def check() -> bool:
        raise RuntimeError("ollama fora do ar")

    app.state.ai_health = check
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_not_found_uses_problem_details(client: TestClient) -> None:
    response = client.get("/nao-existe")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "not_found"
    assert body["status"] == 404
    assert body["type"] == "about:blank"
    assert body["request_id"] == response.headers["x-request-id"]


def test_method_not_allowed_uses_problem_details(client: TestClient) -> None:
    response = client.post("/health")
    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"
    assert response.headers["x-request-id"]


def test_payload_too_large_by_content_length() -> None:
    settings = make_settings(
        max_request_body_bytes=100,
        max_messages_per_request=1,
        max_message_chars=10,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/health", content=b"x" * 200)

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "payload_too_large"
    assert body["request_id"] == response.headers["x-request-id"]


def test_payload_too_large_chunked() -> None:
    settings = make_settings(
        max_request_body_bytes=100,
        max_messages_per_request=1,
        max_message_chars=10,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/health", content=iter([b"x" * 200]))

    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_cors_preflight(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "GET" in response.headers["access-control-allow-methods"]
    assert response.headers["x-request-id"]


def test_cors_exposes_headers(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    exposed = response.headers["access-control-expose-headers"]
    for header in (
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
        "X-Request-ID",
        "Content-Type",
    ):
        assert header in exposed


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "https://malicioso.test"})
    assert "access-control-allow-origin" not in response.headers


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class _ChatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[_Message]


def test_validation_error_format(client: TestClient, app: FastAPI) -> None:
    @app.post("/_test/validation")
    async def _validation(payload: _ChatPayload) -> dict[str, int]:
        return {"ok": len(payload.messages)}

    response = client.post(
        "/_test/validation",
        json={"messages": [{"role": 1, "content": "oi"}], "extra": True},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["status"] == 422
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["errors"]
    fields = {item["field"] for item in body["errors"]}
    assert "messages.0.role" in fields
    assert "extra" in fields
    assert all(item["reason"] for item in body["errors"])


def test_valid_payload_passes_validation(client: TestClient, app: FastAPI) -> None:
    @app.post("/_test/validation-ok")
    async def _validation(payload: _ChatPayload) -> dict[str, int]:
        return {"ok": len(payload.messages)}

    response = client.post(
        "/_test/validation-ok",
        json={"messages": [{"role": "user", "content": "oi"}]},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": 1}


def test_openapi_lists_system_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/health/ready" in paths


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_documentation_is_public(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200

"""Contrato do OpenAPI: cobertura, exemplos e snapshot (F10, CA-17).

O snapshot evita regressões silenciosas no contrato. Para atualizá-lo após uma
mudança intencional:

    UPDATE_OPENAPI=1 pytest tests/integration/test_openapi_contract.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshots" / "openapi.json"

EXPECTED_OPERATIONS: dict[str, set[str]] = {
    "/health": {"get"},
    "/health/db": {"get"},
    "/health/ready": {"get"},
    "/api/v1/auth/login": {"post"},
    "/api/v1/auth/guest": {"post"},
    "/api/v1/usage": {"get"},
    "/api/v1/chat": {"post"},
    "/api/v1/models": {"get"},
    "/api/v1/admin/stats": {"get"},
    "/api/v1/admin/db/status": {"get"},
    "/api/v1/admin/ai/status": {"get"},
    "/api/v1/admin/guests": {"get"},
    "/api/v1/admin/usage/reset": {"post"},
    "/api/v1/admin/users": {"get", "post"},
    "/api/v1/admin/users/bulk": {"post"},
    "/api/v1/admin/users/cleanup": {"post"},
    "/api/v1/admin/users/{user_id}": {"get", "patch", "delete"},
    "/api/v1/admin/users/{user_id}/reset-password": {"post"},
    "/v1/chat/completions": {"post"},
    "/v1/models": {"get"},
    "/v1/messages": {"post"},
    "/v1/messages/count_tokens": {"post"},
    "/api/chat": {"post"},
    "/api/tags": {"get"},
    "/api/show": {"post"},
    "/api/ps": {"get"},
}

PUBLIC_OPERATIONS = {
    ("/health", "get"),
    ("/health/db", "get"),
    ("/health/ready", "get"),
    ("/api/v1/auth/login", "post"),
    ("/api/v1/auth/guest", "post"),
}

DOCUMENTED_EXAMPLE_SCHEMAS = (
    "TokenResponse",
    "GuestTokenResponse",
    "UsageResponse",
    "ChatRequest",
    "ChatResponse",
    "ModelsResponse",
    "StatsResponse",
    "AiStatusResponse",
    "GuestsResponse",
    "ResetRequest",
    "HealthResponse",
    "ReadinessResponse",
    "LoginRequest",
    "DbHealthResponse",
    "DbStatusResponse",
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "UserCreateResponse",
    "UserPage",
    "BulkCreateRequest",
    "BulkCreateResponse",
    "ResetPasswordRequest",
    "ResetPasswordResponse",
    "CleanupResponse",
    "OpenAIChatRequest",
    "OpenAIChatResponse",
    "OpenAIModelList",
    "AnthropicMessageRequest",
    "AnthropicMessageResponse",
    "AnthropicCountTokensRequest",
    "AnthropicCountTokensResponse",
    "OllamaChatRequest",
    "OllamaChatResponse",
    "OllamaTagsResponse",
    "OllamaShowRequest",
    "OllamaShowResponse",
    "OllamaPsResponse",
)


def _spec(client: TestClient) -> dict[str, Any]:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload


def _serialized(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operation)
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    ]


def test_openapi_matches_the_committed_snapshot(client: TestClient) -> None:
    spec = _spec(client)

    if os.environ.get("UPDATE_OPENAPI") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(_serialized(spec), encoding="utf-8")
        pytest.skip("snapshot do OpenAPI atualizado")

    assert SNAPSHOT.exists(), "snapshot ausente; rode com UPDATE_OPENAPI=1"
    assert spec == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_openapi_lists_every_endpoint(client: TestClient) -> None:
    spec = _spec(client)

    assert set(spec["paths"]) == set(EXPECTED_OPERATIONS)
    for path, methods in EXPECTED_OPERATIONS.items():
        assert set(spec["paths"][path]) == methods


def test_every_operation_is_documented(client: TestClient) -> None:
    spec = _spec(client)

    for path, method, operation in _operations(spec):
        assert operation.get("summary"), f"{method.upper()} {path} sem summary"
        assert operation.get("description"), f"{method.upper()} {path} sem description"
        assert operation.get("tags"), f"{method.upper()} {path} sem tag"
        assert operation.get("responses"), f"{method.upper()} {path} sem respostas"
        for code, response in operation["responses"].items():
            assert response.get("description"), f"{method.upper()} {path} {code} sem descrição"


def test_write_operations_document_the_request_body(client: TestClient) -> None:
    spec = _spec(client)

    for path, method, operation in _operations(spec):
        if method != "post":
            continue
        if path in {"/api/v1/auth/guest", "/api/v1/admin/users/cleanup"}:
            assert "requestBody" not in operation
            continue
        assert operation.get("requestBody"), f"POST {path} sem requestBody"
        assert "application/json" in operation["requestBody"]["content"]


def test_chat_documents_json_and_sse(client: TestClient) -> None:
    spec = _spec(client)
    operation = spec["paths"]["/api/v1/chat"]["post"]
    content = operation["responses"]["200"]["content"]

    assert set(content) == {"application/json", "text/event-stream"}
    assert content["application/json"]["schema"]["$ref"].endswith("/ChatResponse")
    sse = content["text/event-stream"]
    assert sse["schema"]["type"] == "string"
    assert "event: done" in sse["example"]
    assert "data:" in sse["example"]


def test_messages_documents_json_and_sse(client: TestClient) -> None:
    spec = _spec(client)
    operation = spec["paths"]["/v1/messages"]["post"]
    content = operation["responses"]["200"]["content"]

    assert set(content) == {"application/json", "text/event-stream"}
    assert content["application/json"]["schema"]["$ref"].endswith("/AnthropicMessageResponse")
    sse = content["text/event-stream"]
    assert sse["schema"]["type"] == "string"
    assert "event: message_start" in sse["example"]
    assert "event: message_stop" in sse["example"]


def test_ollama_chat_documents_json_and_ndjson(client: TestClient) -> None:
    spec = _spec(client)
    operation = spec["paths"]["/api/chat"]["post"]
    content = operation["responses"]["200"]["content"]

    assert set(content) == {"application/json", "application/x-ndjson"}
    assert content["application/json"]["schema"]["$ref"].endswith("/OllamaChatResponse")
    ndjson = content["application/x-ndjson"]
    assert ndjson["schema"]["type"] == "string"
    lines = [line for line in ndjson["example"].splitlines() if line]
    assert all(json.loads(line) for line in lines)
    assert json.loads(lines[-1])["done"] is True


def test_protected_operations_use_bearer_auth(client: TestClient) -> None:
    spec = _spec(client)
    schemes = spec["components"]["securitySchemes"]

    assert schemes["HTTPBearer"]["type"] == "http"
    assert schemes["HTTPBearer"]["scheme"] == "bearer"

    for path, method, operation in _operations(spec):
        expected = [{"HTTPBearer": []}]
        if (path, method) in PUBLIC_OPERATIONS:
            assert "security" not in operation, f"{method.upper()} {path} não é público"
        else:
            assert operation.get("security") == expected, f"{method.upper()} {path} sem security"


def test_documented_schemas_carry_examples(client: TestClient) -> None:
    spec = _spec(client)
    schemas = spec["components"]["schemas"]

    for name in DOCUMENTED_EXAMPLE_SCHEMAS:
        assert schemas[name].get("example"), f"schema {name} sem exemplo"

"""Contrato da compatibilidade Ollama (F16, CA-37/CA-38/CA-39)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.ai.base import ProviderError, ProviderTimeout, ProviderUnavailable, ToolCall
from app.ai.fake import FakeProvider
from app.main import create_app
from tests.conftest import FakeClock, make_settings

API_KEY = "nxa_test_integration_0001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
MESSAGES = [{"role": "user", "content": "Olá"}]


@contextmanager
def client_for(
    *,
    provider: FakeProvider | None = None,
    client_address: tuple[str, int] = ("testclient", 50000),
    **overrides: object,
) -> Iterator[tuple[TestClient, FakeProvider]]:
    settings = make_settings(compat_api_keys=API_KEY, **overrides)
    fake = provider or FakeProvider()
    clock = FakeClock(datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
    with TestClient(
        create_app(settings, clock=clock, provider=fake),
        client=client_address,
    ) as client:
        yield client, fake


def _chat(
    client: TestClient,
    body: dict[str, object] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> object:
    payload: dict[str, object] = {
        "model": "fake/fake-model",
        "messages": MESSAGES,
        "stream": False,
    }
    payload.update(body or {})
    return client.post(
        "/api/chat",
        headers=AUTH if headers is None else headers,
        json=payload,
    )


def _lines(response: object) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in response.text.splitlines()  # type: ignore[attr-defined]
        if line.strip()
    ]


def _admin_token(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    token: str = response.json()["access_token"]
    return token


def test_json_chat_returns_ollama_shape(provider: FakeProvider) -> None:
    with client_for(provider=provider) as (client, _):
        response = _chat(client)

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "fake-model:latest"
    assert body["created_at"] == "2026-10-29T12:00:00Z"
    assert body["message"] == {
        "role": "assistant",
        "content": "Olá do fake!",
        "tool_calls": [],
    }
    assert body["done"] is True
    assert body["done_reason"] == "stop"
    assert body["prompt_eval_count"] == 11
    assert body["eval_count"] == 7
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "59"
    assert API_KEY not in response.text
    assert provider.calls[0].model == "fake-model:latest"


def test_stream_is_ndjson_and_ends_with_done(provider: FakeProvider) -> None:
    with client_for(provider=provider) as (client, _):
        response = client.post(
            "/api/chat",
            headers=AUTH,
            json={"model": "fake/fake-model", "messages": MESSAGES},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = _lines(response)
    assert len(lines) == 3
    assert all(line["done"] is False for line in lines[:-1])
    assert "".join(str(line["message"]["content"]) for line in lines[:-1]) == "Olá do fake!"
    assert lines[-1]["done"] is True
    assert lines[-1]["done_reason"] == "stop"
    assert lines[-1]["message"]["content"] == ""
    assert lines[-1]["prompt_eval_count"] == 11
    assert lines[-1]["eval_count"] == 7


def test_stream_tool_calls_are_returned_in_the_final_line(provider: FakeProvider) -> None:
    provider.chunks = ()
    provider.tool_calls = (ToolCall(id="call_1", name="clima", arguments={"cidade": "Recife"}),)
    provider.finish_reason = "tool_use"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "clima",
                "description": "Consulta o clima",
                "parameters": {"type": "object", "properties": {"cidade": {"type": "string"}}},
            },
        }
    ]

    with client_for(provider=provider) as (client, _):
        response = client.post(
            "/api/chat",
            headers=AUTH,
            json={"model": "fake/fake-model", "messages": MESSAGES, "tools": tools},
        )

    assert response.status_code == 200
    lines = _lines(response)
    assert len(lines) == 1
    assert lines[-1]["done"] is True
    assert lines[-1]["done_reason"] == "tool_calls"
    assert lines[-1]["message"]["tool_calls"] == [
        {"function": {"name": "clima", "arguments": {"cidade": "Recife"}}}
    ]
    assert provider.calls[0].tools is not None
    assert provider.calls[0].tools[0].name == "clima"


def test_tools_are_translated_to_and_from_ollama(provider: FakeProvider) -> None:
    provider.tool_calls = (ToolCall(id="call_1", name="clima", arguments={"cidade": "Recife"}),)
    provider.finish_reason = "tool_use"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "clima",
                "description": "Consulta o clima",
                "parameters": {"type": "object", "properties": {"cidade": {"type": "string"}}},
            },
        }
    ]

    with client_for(provider=provider) as (client, _):
        response = _chat(client, {"tools": tools})

    assert response.status_code == 200
    body = response.json()
    assert body["done_reason"] == "tool_calls"
    assert body["message"]["tool_calls"] == [
        {"function": {"name": "clima", "arguments": {"cidade": "Recife"}}}
    ]
    recorded = provider.calls[0]
    assert recorded.tools is not None
    assert recorded.tools[0].name == "clima"
    assert recorded.tools[0].parameters["type"] == "object"


def test_tool_calls_and_results_in_messages_are_converted(provider: FakeProvider) -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "Seja breve."},
            {"role": "user", "content": "Como está o tempo?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "clima", "arguments": {"cidade": "Recife"}}}],
            },
            {"role": "tool", "tool_name": "clima", "content": "Sol"},
        ]
    }

    with client_for(provider=provider) as (client, _):
        response = _chat(client, payload)

    assert response.status_code == 200
    messages = provider.calls[0].messages
    assert [message.role for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert messages[2].tool_calls[0].name == "clima"
    assert messages[2].tool_calls[0].arguments == {"cidade": "Recife"}
    assert messages[3].content == "Sol"
    assert messages[3].tool_call_id == "clima"


def test_options_num_predict_is_capped_by_server_limit(provider: FakeProvider) -> None:
    with client_for(provider=provider, ai_max_output_tokens=100) as (client, _):
        _chat(client, {"options": {"num_predict": 50}})
        _chat(client, {"options": {"num_predict": 500}})
        _chat(client, {"options": {"num_predict": -1}})

    assert [call.max_tokens for call in provider.calls] == [50, 100, 100]


def test_format_requires_json_mode_capability() -> None:
    with client_for() as (client, _):
        accepted = _chat(client, {"format": "json"})
        denied = _chat(client, {"model": "fake/fake-small", "format": "json"})

    assert accepted.status_code == 200
    assert denied.status_code == 400
    assert denied.json()["error"] == "O modelo escolhido não suporta a capacidade exigida."


def test_images_require_vision_capability() -> None:
    with client_for() as (client, _):
        denied = _chat(
            client,
            {
                "model": "fake/fake-small",
                "messages": [{"role": "user", "content": "O que é?", "images": ["base64..."]}],
            },
        )

    assert denied.status_code == 400
    assert "error" in denied.json()


def test_tags_returns_ollama_shape() -> None:
    with client_for() as (client, _):
        response = client.get("/api/tags", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    names = {model["name"] for model in body["models"]}
    assert names == {"fake/fake-model", "fake/fake-small"}
    for model in body["models"]:
        assert model["model"] == model["name"]
        assert model["modified_at"].endswith("Z")
        assert model["size"] > 0
        assert model["digest"].startswith("sha256:")
        assert model["details"]["family"] == "fake"
        assert model["details"]["parameter_size"] in {"1B", "0.5B"}
        assert model["details"]["quantization_level"] in {"Q4_K_M", "Q8_0"}


def test_show_accepts_canonical_id_and_plain_name() -> None:
    with client_for() as (client, _):
        canonical = client.post("/api/show", headers=AUTH, json={"model": "fake/fake-model"})
        plain = client.post("/api/show", headers=AUTH, json={"model": "fake-model"})
        missing = client.post("/api/show", headers=AUTH, json={"model": "fake/nao-existe"})

    assert canonical.status_code == 200
    body = canonical.json()
    assert body["model"] == "fake/fake-model"
    assert body["details"]["family"] == "fake"
    assert body["capabilities"] == ["completion", "tools", "vision"]
    assert body["modified_at"].endswith("Z")
    assert plain.status_code == 200
    assert plain.json()["model"] == "fake/fake-model"
    assert missing.status_code == 404
    assert missing.json() == {"error": "model 'fake/nao-existe' not found"}


def test_ps_returns_empty_list() -> None:
    with client_for() as (client, _):
        response = client.get("/api/ps", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"models": []}


def test_missing_or_invalid_api_key_uses_ollama_envelope() -> None:
    with client_for() as (client, _):
        missing = client.post("/api/chat", json={"model": "fake/fake-model", "messages": MESSAGES})
        invalid = client.get("/api/tags", headers={"Authorization": "Bearer nxa_errada"})

    for response in (missing, invalid):
        assert response.status_code == 401
        assert isinstance(response.json()["error"], str)
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_localhost_access_is_opt_in() -> None:
    with client_for(client_address=("127.0.0.1", 50000)) as (client, _):
        disabled = client.get("/api/tags")
    assert disabled.status_code == 401

    with client_for(
        client_address=("127.0.0.1", 50000),
        compat_ollama_allow_localhost=True,
    ) as (client, _):
        enabled = client.get("/api/tags")
    assert enabled.status_code == 200

    with client_for(
        client_address=("10.0.0.7", 50000),
        compat_ollama_allow_localhost=True,
    ) as (client, _):
        remote = client.get("/api/tags")
    assert remote.status_code == 401


def test_admin_jwt_is_accepted() -> None:
    with client_for() as (client, _):
        token = _admin_token(client)
        response = _chat(client, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


def test_validation_errors_use_ollama_envelope() -> None:
    with client_for() as (client, _):
        response = client.post(
            "/api/chat",
            headers=AUTH,
            json={"messages": MESSAGES},
        )

    assert response.status_code == 400
    assert isinstance(response.json()["error"], str)


def test_unknown_api_route_uses_ollama_envelope() -> None:
    with client_for() as (client, _):
        response = client.get("/api/inexistente", headers=AUTH)

    assert response.status_code == 404
    assert isinstance(response.json()["error"], str)


def test_native_api_v1_keeps_problem_details() -> None:
    with client_for() as (client, _):
        response = client.get("/api/v1/inexistente", headers=AUTH)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "type" in response.json()


def test_provider_failures_are_masked() -> None:
    for error, status_code in (
        (ProviderError("segredo interno do provedor"), 502),
        (ProviderTimeout("timeout simulado"), 504),
        (ProviderUnavailable("segredo interno indisponível"), 503),
    ):
        provider = FakeProvider(error=error)
        with client_for(provider=provider) as (client, _):
            response = _chat(client)
        assert response.status_code == status_code
        assert isinstance(response.json()["error"], str)
        assert "segredo interno" not in response.text
        assert "timeout simulado" not in response.text


def test_stream_errors_use_an_error_line() -> None:
    provider = FakeProvider(error="error")

    with client_for(provider=provider) as (client, _):
        response = client.post(
            "/api/chat",
            headers=AUTH,
            json={"model": "fake/fake-model", "messages": MESSAGES},
        )

    assert response.status_code == 200
    lines = _lines(response)
    assert len(lines) == 1
    assert lines[0] == {"error": "O provedor de IA retornou um erro."}
    assert all(line.get("done") is not True for line in lines)


def test_api_key_rate_limit_returns_429() -> None:
    with client_for(compat_api_key_rate_per_min=1) as (client, _):
        first = client.get("/api/tags", headers=AUTH)
        second = client.get("/api/tags", headers=AUTH)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"] == (
        "Muitas requisições em pouco tempo. Tente novamente em instantes."
    )
    assert int(second.headers["Retry-After"]) >= 1


def test_keep_alive_and_stop_options_are_accepted(provider: FakeProvider) -> None:
    with client_for(provider=provider) as (client, _):
        response = _chat(
            client,
            {
                "keep_alive": "5m",
                "options": {"temperature": 0.2, "top_p": 0.9, "stop": ["\n\n"]},
            },
        )

    assert response.status_code == 200
    assert provider.calls[0].max_tokens == 1024


def test_cors_preflight_for_api_chat() -> None:
    with client_for(cors_origins="http://app.test") as (client, _):
        response = client.options(
            "/api/chat",
            headers={
                "Origin": "http://app.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://app.test"

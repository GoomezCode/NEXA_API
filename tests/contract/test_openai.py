"""Contrato da compatibilidade OpenAI (F14, CA-31..CA-33/CA-38/CA-39)."""

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
    **overrides: object,
) -> Iterator[tuple[TestClient, FakeProvider]]:
    settings = make_settings(compat_api_keys=API_KEY, **overrides)
    fake = provider or FakeProvider()
    clock = FakeClock(datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
    with TestClient(create_app(settings, clock=clock, provider=fake)) as client:
        yield client, fake


def _chat(
    client: TestClient,
    body: dict[str, object] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> object:
    payload: dict[str, object] = {"model": "fake/fake-model", "messages": MESSAGES}
    payload.update(body or {})
    return client.post(
        "/v1/chat/completions",
        headers=AUTH if headers is None else headers,
        json=payload,
    )


def _admin_token(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    token: str = response.json()["access_token"]
    return token


def test_json_chat_returns_openai_shape(provider: FakeProvider) -> None:
    with client_for(provider=provider) as (client, _):
        response = _chat(client)

    assert response.status_code == 200
    body = response.json()
    assert body["id"].startswith("chatcmpl-nexa-")
    assert body["object"] == "chat.completion"
    assert body["created"] == int(datetime(2026, 10, 29, 12, 0, tzinfo=UTC).timestamp())
    assert body["model"] == "fake-model:latest"
    assert body["choices"] == [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Olá do fake!",
                "tool_calls": [],
            },
            "finish_reason": "stop",
        }
    ]
    assert body["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "59"
    assert API_KEY not in response.text
    assert provider.calls[0].model == "fake-model:latest"


def test_stream_emits_chunks_and_done(provider: FakeProvider) -> None:
    with client_for(provider=provider) as (client, _):
        response = _chat(client, {"stream": True, "stream_options": {"include_usage": True}})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert response.text.rstrip().endswith("data: [DONE]")
    assert [event["object"] for event in events] == ["chat.completion.chunk"] * len(events)
    assert events[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    contents = [
        event["choices"][0]["delta"]["content"]
        for event in events
        if event["choices"] and "content" in event["choices"][0]["delta"]
    ]
    assert "".join(contents) == "Olá do fake!"
    assert events[-2]["choices"][0]["finish_reason"] == "stop"
    assert events[-1]["choices"] == []
    assert events[-1]["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


def test_stream_without_chunks_terminates_with_done(provider: FakeProvider) -> None:
    provider.chunks = ()

    with client_for(provider=provider) as (client, _):
        response = _chat(client, {"stream": True})

    assert response.status_code == 200
    assert response.text.rstrip().endswith("data: [DONE]")
    assert '"finish_reason":"stop"' in response.text


def test_tools_are_translated_to_and_from_openai(provider: FakeProvider) -> None:
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
        response = _chat(client, {"tools": tools, "tool_choice": "required"})

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    call = body["choices"][0]["message"]["tool_calls"][0]
    assert call == {
        "id": "call_1",
        "type": "function",
        "function": {"name": "clima", "arguments": '{"cidade":"Recife"}'},
    }
    recorded = provider.calls[0]
    assert recorded.tools is not None
    assert recorded.tools[0].name == "clima"
    assert recorded.tool_choice == "required"


def test_assistant_tool_calls_are_converted_to_internal_messages(
    provider: FakeProvider,
) -> None:
    payload = {
        "messages": [
            {"role": "user", "content": "Como está o tempo?"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "clima", "arguments": '{"cidade":"Recife"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "Sol"},
        ]
    }

    with client_for(provider=provider) as (client, _):
        response = _chat(client, payload)

    assert response.status_code == 200
    messages = provider.calls[0].messages
    assert messages[-2].tool_calls[0].name == "clima"
    assert messages[-1].role == "tool"
    assert messages[-1].tool_call_id == "call_1"


def test_invalid_tool_arguments_return_invalid_value() -> None:
    payload = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "clima", "arguments": "{nao-json"},
                    }
                ],
            }
        ]
    }

    with client_for() as (client, _):
        response = _chat(client, payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_value"


def test_unsupported_parameters_return_400() -> None:
    with client_for() as (client, _):
        for key, value in (("n", 2), ("logprobs", True)):
            response = _chat(client, {key: value})
            assert response.status_code == 400
            assert response.json()["error"] == {
                "message": response.json()["error"]["message"],
                "type": "invalid_request_error",
                "param": key,
                "code": "unsupported_parameter",
            }


def test_models_endpoint_returns_openai_list() -> None:
    with client_for() as (client, _):
        response = client.get("/v1/models", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert {model["id"] for model in body["data"]} == {
        "fake/fake-model",
        "fake/fake-small",
    }
    assert all(model["object"] == "model" for model in body["data"])
    assert all(model["owned_by"] == "nexa" for model in body["data"])


def test_missing_or_invalid_api_key_uses_openai_envelope() -> None:
    with client_for() as (client, _):
        missing = client.post(
            "/v1/chat/completions", json={"model": "fake/fake-model", "messages": MESSAGES}
        )
        invalid = _chat(client, headers={"Authorization": "Bearer nxa_errada"})
        bad_scheme = _chat(client, headers={"Authorization": "Basic abc"})

    for response in (missing, invalid, bad_scheme):
        assert response.status_code == 401
        assert response.json()["error"]["type"] == "authentication_error"
        assert response.json()["error"]["code"] == "invalid_api_key"
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_x_api_key_header_is_accepted() -> None:
    with client_for() as (client, _):
        response = _chat(client, headers={"x-api-key": API_KEY})

    assert response.status_code == 200


def test_admin_jwt_is_accepted() -> None:
    with client_for() as (client, _):
        token = _admin_token(client)
        response = _chat(client, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


def test_model_aliases_and_allowlist_are_applied() -> None:
    with client_for(ai_model_aliases={"fast": "fake/fake-model"}) as (client, _):
        alias = _chat(client, {"model": "fast"})
    assert alias.status_code == 200

    with client_for(ai_allowed_models=["fake/fake-model"]) as (client, _):
        denied = _chat(client, {"model": "fake/fake-small"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "model_not_allowed"


def test_unknown_model_returns_404() -> None:
    with client_for() as (client, _):
        response = _chat(client, {"model": "fake/nao-existe"})

    assert response.status_code == 404
    assert response.json()["error"] == {
        "message": "O modelo informado não existe.",
        "type": "invalid_request_error",
        "param": "model",
        "code": "model_not_found",
    }


def test_validation_errors_use_openai_envelope() -> None:
    with client_for() as (client, _):
        response = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "Content-Type": "application/json"},
            content="{nao-json",
        )

    assert response.status_code == 422
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert response.json()["error"]["code"] == "validation_error"


def test_api_key_rate_limit_returns_429() -> None:
    with client_for(compat_api_key_rate_per_min=1) as (client, _):
        first = _chat(client)
        second = _chat(client)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "rate_limit_error"
    assert second.json()["error"]["code"] == "rate_limited"
    assert int(second.headers["Retry-After"]) >= 1


def test_provider_failures_are_masked() -> None:
    for error, status_code, code in (
        (ProviderError("segredo interno do provedor"), 502, "ai_provider_error"),
        (ProviderTimeout("timeout simulado"), 504, "ai_provider_timeout"),
        (ProviderUnavailable("segredo interno indisponível"), 503, "provider_unavailable"),
    ):
        provider = FakeProvider(error=error)
        with client_for(provider=provider) as (client, _):
            response = _chat(client)
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code
        assert "segredo interno" not in response.text
        assert "timeout simulado" not in response.text


def test_stream_errors_use_openai_envelope_without_done() -> None:
    provider = FakeProvider(error="error")

    with client_for(provider=provider) as (client, _):
        response = _chat(client, {"stream": True})

    assert response.status_code == 200
    assert '"error"' in response.text
    assert '"code":"ai_provider_error"' in response.text
    assert "data: [DONE]" not in response.text


def test_max_tokens_is_capped_by_server_limit(provider: FakeProvider) -> None:
    with client_for(provider=provider, ai_max_output_tokens=100) as (client, _):
        _chat(client, {"max_tokens": 50})
        _chat(client, {"max_tokens": 500})

    assert [call.max_tokens for call in provider.calls] == [50, 100]


def test_unsupported_capabilities_return_422() -> None:
    tools = [
        {
            "type": "function",
            "function": {"name": "clima", "parameters": {"type": "object"}},
        }
    ]

    with client_for() as (client, _):
        tools_response = _chat(client, {"model": "fake/fake-small", "tools": tools})
        json_response = _chat(
            client,
            {"model": "fake/fake-small", "response_format": {"type": "json_object"}},
        )

    assert tools_response.status_code == 422
    assert tools_response.json()["error"]["code"] == "unsupported_capability"
    assert tools_response.json()["error"]["param"] == "tools"
    assert json_response.status_code == 422
    assert json_response.json()["error"]["param"] == "model"


def test_test_user_jwt_consumes_the_same_quota() -> None:
    with client_for() as (client, _):
        admin = _admin_token(client)
        created = client.post(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {admin}"},
            json={
                "username": "compatuser",
                "password": "senha-forte",
                "daily_message_limit": 1,
                "rate_per_min": 5,
            },
        )
        assert created.status_code == 201
        token = client.post(
            "/api/v1/auth/login",
            json={"username": "compatuser", "password": "senha-forte"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        first = _chat(client, headers=headers)
        second = _chat(client, headers=headers)

    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "1"
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "quota_exceeded"
    assert second.json()["error"]["type"] == "rate_limit_error"


def test_cors_preflight_for_v1() -> None:
    with client_for(cors_origins="http://app.test") as (client, _):
        response = client.options(
            "/v1/chat/completions",
            headers={
                "Origin": "http://app.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://app.test"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_unknown_v1_route_uses_openai_envelope() -> None:
    with client_for() as (client, _):
        response = client.get("/v1/inexistente", headers=AUTH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_anonymous_mode_is_opt_in() -> None:
    with client_for() as (client, _):
        forbidden = _chat(client, headers={})
    assert forbidden.status_code == 401

    with client_for(compat_allow_anonymous=True) as (client, _):
        allowed = _chat(client, headers={})
    assert allowed.status_code == 200


def test_guest_jwt_system_messages_are_rejected() -> None:
    with client_for() as (client, _):
        token = client.post("/api/v1/auth/guest").json()["access_token"]
        response = _chat(
            client,
            {"messages": [{"role": "system", "content": "regra"}, *MESSAGES]},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["param"] == "messages.0.role"

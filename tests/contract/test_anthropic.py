"""Contrato da compatibilidade Anthropic (F15, CA-34/CA-35/CA-38/CA-39)."""

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
AUTH = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01"}
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


def _message(
    client: TestClient,
    body: dict[str, object] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> object:
    payload: dict[str, object] = {
        "model": "fake/fake-model",
        "max_tokens": 256,
        "messages": MESSAGES,
    }
    payload.update(body or {})
    return client.post(
        "/v1/messages",
        headers=AUTH if headers is None else headers,
        json=payload,
    )


def _count(
    client: TestClient,
    body: dict[str, object] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> object:
    payload: dict[str, object] = {"model": "fake/fake-model", "messages": MESSAGES}
    payload.update(body or {})
    return client.post(
        "/v1/messages/count_tokens",
        headers=AUTH if headers is None else headers,
        json=payload,
    )


def _events(response: object) -> list[tuple[str, dict[str, object]]]:
    text = response.text  # type: ignore[attr-defined]
    events: list[tuple[str, dict[str, object]]] = []
    name = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            events.append((name, json.loads(line.removeprefix("data: "))))
    return events


def _admin_token(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    token: str = response.json()["access_token"]
    return token


def test_json_message_returns_anthropic_shape(provider: FakeProvider) -> None:
    with client_for(provider=provider) as (client, _):
        response = _message(client)

    assert response.status_code == 200
    body = response.json()
    assert body["id"].startswith("msg_nexa_")
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["model"] == "fake-model:latest"
    assert body["content"] == [{"type": "text", "text": "Olá do fake!"}]
    assert body["stop_reason"] == "end_turn"
    assert body["stop_sequence"] is None
    assert body["usage"] == {"input_tokens": 11, "output_tokens": 7}
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "59"
    assert API_KEY not in response.text
    assert provider.calls[0].model == "fake-model:latest"


def test_system_accepts_string_and_blocks(provider: FakeProvider) -> None:
    with client_for(provider=provider) as (client, _):
        string_response = _message(client, {"system": "Seja breve."})
        blocks_response = _message(
            client,
            {
                "system": [
                    {"type": "text", "text": "Regra A."},
                    {"type": "text", "text": "Regra B."},
                ]
            },
        )

    assert string_response.status_code == 200
    assert blocks_response.status_code == 200
    assert provider.calls[0].messages[0].role == "system"
    assert provider.calls[0].messages[0].content == "Seja breve."
    assert provider.calls[1].messages[0].content == "Regra A.\n\nRegra B."


def test_tools_and_tool_use_are_translated(provider: FakeProvider) -> None:
    provider.tool_calls = (ToolCall(id="toolu_1", name="clima", arguments={"cidade": "Recife"}),)
    provider.finish_reason = "tool_use"
    tools = [
        {
            "name": "clima",
            "description": "Consulta o clima",
            "input_schema": {
                "type": "object",
                "properties": {"cidade": {"type": "string"}},
            },
        }
    ]

    with client_for(provider=provider) as (client, _):
        response = _message(client, {"tools": tools, "tool_choice": {"type": "any"}})

    assert response.status_code == 200
    body = response.json()
    assert body["stop_reason"] == "tool_use"
    assert body["content"] == [
        {"type": "text", "text": "Olá do fake!"},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "clima",
            "input": {"cidade": "Recife"},
        },
    ]
    recorded = provider.calls[0]
    assert recorded.tools is not None
    assert recorded.tools[0].name == "clima"
    assert recorded.tools[0].parameters["type"] == "object"
    assert recorded.tool_choice == "required"


def test_tool_result_and_assistant_tool_use_history(provider: FakeProvider) -> None:
    payload = {
        "messages": [
            {"role": "user", "content": "Como está o tempo?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Vou consultar."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "clima",
                        "input": {"cidade": "Recife"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "Sol"}],
                    },
                    {"type": "text", "text": "E amanhã?"},
                ],
            },
        ]
    }

    with client_for(provider=provider) as (client, _):
        response = _message(client, payload)

    assert response.status_code == 200
    messages = provider.calls[0].messages
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert messages[1].content == "Vou consultar."
    assert messages[1].tool_calls[0].name == "clima"
    assert messages[2].tool_call_id == "toolu_1"
    assert messages[2].content == "Sol"
    assert messages[3].content == "E amanhã?"


def test_stream_emits_the_anthropic_event_sequence(provider: FakeProvider) -> None:
    with client_for(provider=provider) as (client, _):
        response = _message(client, {"stream": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    names = [name for name, _ in events]
    assert names == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    start = events[0][1]["message"]
    assert start["id"].startswith("msg_nexa_")
    assert start["content"] == []
    assert start["stop_reason"] is None
    text = "".join(
        str(payload["delta"]["text"]) for name, payload in events if name == "content_block_delta"
    )
    assert text == "Olá do fake!"
    assert events[-2][1]["delta"]["stop_reason"] == "end_turn"
    assert events[-1][1] == {"type": "message_stop"}


def test_stream_with_tools_emits_tool_use_block(provider: FakeProvider) -> None:
    provider.chunks = ()
    provider.tool_calls = (ToolCall(id="toolu_1", name="list_files", arguments={"path": "."}),)
    provider.finish_reason = "tool_use"
    tools = [
        {
            "name": "list_files",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]

    with client_for(provider=provider) as (client, _):
        response = _message(
            client,
            {"stream": True, "tools": tools, "tool_choice": {"type": "auto"}},
        )

    events = _events(response)
    names = [name for name, _ in events]
    assert names == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    block = events[1][1]["content_block"]
    assert block == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "list_files",
        "input": {},
    }
    delta = events[2][1]["delta"]
    assert delta["type"] == "input_json_delta"
    assert json.loads(delta["partial_json"]) == {"path": "."}
    assert events[-2][1]["delta"]["stop_reason"] == "tool_use"
    assert provider.calls[0].tools is not None
    assert provider.calls[0].tool_choice == "auto"


def test_count_tokens_uses_the_provider_count(provider: FakeProvider) -> None:
    provider.token_count = 42

    with client_for(provider=provider) as (client, _):
        response = _count(client)

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 42}
    assert provider.count_calls == ["fake-model:latest"]
    assert provider.calls == []


def test_count_tokens_falls_back_to_the_documented_estimate(
    provider: FakeProvider,
) -> None:
    provider.token_count = None
    text = "x" * 41

    with client_for(provider=provider) as (client, _):
        response = _count(client, {"messages": [{"role": "user", "content": text}]})

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 11}


def test_count_tokens_does_not_consume_quota() -> None:
    with client_for() as (client, _):
        admin = _admin_token(client)
        created = client.post(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {admin}"},
            json={
                "username": "countuser",
                "password": "senha-forte",
                "daily_message_limit": 1,
                "rate_per_min": 10,
            },
        )
        assert created.status_code == 201
        token = client.post(
            "/api/v1/auth/login",
            json={"username": "countuser", "password": "senha-forte"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        first = _count(client, headers=headers)
        second = _count(client, headers=headers)
        message = _message(client, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert message.status_code == 200


def test_missing_or_invalid_api_key_uses_anthropic_envelope() -> None:
    with client_for() as (client, _):
        missing = client.post(
            "/v1/messages",
            headers={"anthropic-version": "2023-06-01"},
            json={"model": "fake/fake-model", "max_tokens": 10, "messages": MESSAGES},
        )
        invalid = _message(client, headers={"x-api-key": "nxa_errada"})
        bearer = _message(client, headers={"Authorization": "Bearer nxa_errada"})

    for response in (missing, invalid, bearer):
        assert response.status_code == 401
        assert response.json() == {
            "type": "error",
            "error": {
                "type": "authentication_error",
                "message": "Chave de API ausente ou inválida.",
            },
        }


def test_x_api_key_and_bearer_are_both_accepted() -> None:
    with client_for() as (client, _):
        by_header = _message(client)
        by_bearer = _message(client, headers={"Authorization": f"Bearer {API_KEY}"})

    assert by_header.status_code == 200
    assert by_bearer.status_code == 200


def test_admin_jwt_is_accepted() -> None:
    with client_for() as (client, _):
        token = _admin_token(client)
        response = _message(client, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


def test_anthropic_version_and_beta_headers_are_accepted() -> None:
    with client_for() as (client, _):
        response = _message(
            client,
            headers={
                **AUTH,
                "anthropic-beta": "prompt-caching-2024-07-31",
            },
        )

    assert response.status_code == 200


def test_unknown_model_uses_not_found_error() -> None:
    with client_for() as (client, _):
        response = _message(client, {"model": "fake/nao-existe"})

    assert response.status_code == 404
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "not_found_error",
            "message": "O modelo informado não existe.",
        },
    }


def test_missing_max_tokens_uses_anthropic_envelope() -> None:
    with client_for() as (client, _):
        response = client.post(
            "/v1/messages",
            headers=AUTH,
            json={"model": "fake/fake-model", "messages": MESSAGES},
        )

    assert response.status_code == 422
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_unknown_content_block_returns_invalid_request_error() -> None:
    with client_for() as (client, _):
        response = _message(
            client,
            {"messages": [{"role": "user", "content": [{"type": "audio"}]}]},
        )

    assert response.status_code == 400
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "audio" in response.json()["error"]["message"]


def test_unknown_v1_messages_route_uses_anthropic_envelope() -> None:
    with client_for() as (client, _):
        response = client.get("/v1/messages/inexistente", headers=AUTH)

    assert response.status_code == 404
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "not_found_error"


def test_provider_failures_are_masked_in_the_anthropic_envelope() -> None:
    for error, status_code in (
        (ProviderError("segredo interno do provedor"), 502),
        (ProviderTimeout("timeout simulado"), 504),
        (ProviderUnavailable("segredo interno indisponível"), 503),
    ):
        provider = FakeProvider(error=error)
        with client_for(provider=provider) as (client, _):
            response = _message(client)
        assert response.status_code == status_code
        assert response.json()["type"] == "error"
        assert response.json()["error"]["type"] == "api_error"
        assert "segredo interno" not in response.text
        assert "timeout simulado" not in response.text


def test_stream_errors_use_typed_error_event() -> None:
    provider = FakeProvider(error="error")

    with client_for(provider=provider) as (client, _):
        response = _message(client, {"stream": True})

    assert response.status_code == 200
    events = _events(response)
    assert events[-1][0] == "error"
    assert events[-1][1]["type"] == "error"
    assert events[-1][1]["error"]["type"] == "api_error"
    assert all(name != "message_stop" for name, _ in events)


def test_max_tokens_is_capped_by_server_limit(provider: FakeProvider) -> None:
    with client_for(provider=provider, ai_max_output_tokens=100) as (client, _):
        _message(client, {"max_tokens": 50})
        _message(client, {"max_tokens": 500})

    assert [call.max_tokens for call in provider.calls] == [50, 100]


def test_unsupported_capabilities_return_invalid_request_error() -> None:
    tools = [{"name": "clima", "input_schema": {"type": "object"}}]

    with client_for() as (client, _):
        response = _message(client, {"model": "fake/fake-small", "tools": tools})

    assert response.status_code == 422
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_api_key_rate_limit_returns_429() -> None:
    with client_for(compat_api_key_rate_per_min=1) as (client, _):
        first = _message(client)
        second = _message(client)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "rate_limit_error"
    assert int(second.headers["Retry-After"]) >= 1


def test_cors_preflight_for_messages() -> None:
    with client_for(cors_origins="http://app.test") as (client, _):
        response = client.options(
            "/v1/messages",
            headers={
                "Origin": "http://app.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-api-key,anthropic-version,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://app.test"
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "x-api-key" in allowed
    assert "anthropic-version" in allowed

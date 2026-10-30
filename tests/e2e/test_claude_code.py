"""Fluxo E2E do Claude Code contra o provedor fake (F15, CA-36).

O teste reproduz o ciclo que o cliente faz contra a Messages API:

1. `count_tokens` antes de enviar a conversa;
2. `/v1/messages` em streaming com `system`, `tools` e `tool_use`;
3. devolução do `tool_result` e nova chamada em streaming com a resposta final.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.ai.base import ToolCall
from app.ai.fake import FakeProvider
from app.main import create_app
from tests.conftest import FakeClock, make_settings

API_KEY = "nxa_test_claude_code_0001"
HEADERS = {
    "x-api-key": API_KEY,
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "prompt-caching-2024-07-31",
}
TOOLS = [
    {
        "name": "list_files",
        "description": "Lista arquivos de um diretório",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
]


@contextmanager
def claude_code(provider: FakeProvider) -> Iterator[TestClient]:
    settings = make_settings(compat_api_keys=API_KEY)
    clock = FakeClock(datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
    with TestClient(create_app(settings, clock=clock, provider=provider)) as client:
        yield client


def events(response: object) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    name = ""
    for line in response.text.splitlines():  # type: ignore[attr-defined]
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            parsed.append((name, json.loads(line.removeprefix("data: "))))
    return parsed


def test_claude_code_tool_round_trip(provider: FakeProvider) -> None:
    provider.chunks = ("Vou listar.",)
    provider.tool_calls = (
        ToolCall(id="toolu_claude_1", name="list_files", arguments={"path": "."}),
    )
    provider.finish_reason = "tool_use"

    with claude_code(provider) as client:
        counted = client.post(
            "/v1/messages/count_tokens",
            headers=HEADERS,
            json={
                "model": "fake/fake-model",
                "system": [{"type": "text", "text": "Você é o Claude Code."}],
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Liste os arquivos."}]}
                ],
                "tools": TOOLS,
            },
        )
        assert counted.status_code == 200
        assert counted.json()["input_tokens"] > 0

        first = client.post(
            "/v1/messages",
            headers=HEADERS,
            json={
                "model": "fake/fake-model",
                "max_tokens": 1024,
                "stream": True,
                "system": [
                    {"type": "text", "text": "Você é o Claude Code."},
                    {"type": "text", "text": "Use as ferramentas disponíveis."},
                ],
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Liste os arquivos."}]}
                ],
                "tools": TOOLS,
                "tool_choice": {"type": "auto"},
            },
        )

        assert first.status_code == 200
        assert first.headers["content-type"].startswith("text/event-stream")
        first_events = events(first)
        assert first_events[0][0] == "message_start"
        assert first_events[-1][0] == "message_stop"
        tool_block = next(
            payload["content_block"]
            for name, payload in first_events
            if name == "content_block_start" and payload["content_block"]["type"] == "tool_use"  # type: ignore[index]
        )
        assert tool_block["id"] == "toolu_claude_1"
        assert tool_block["name"] == "list_files"
        assert (
            next(
                payload["delta"]["stop_reason"]
                for name, payload in first_events
                if name == "message_delta"
            )
            == "tool_use"
        )

        # O cliente executa a ferramenta e devolve o resultado.
        provider.chunks = ("Encontrei 2 arquivos.",)
        provider.tool_calls = ()
        provider.finish_reason = "stop"
        second = client.post(
            "/v1/messages",
            headers=HEADERS,
            json={
                "model": "fake/fake-model",
                "max_tokens": 1024,
                "stream": True,
                "system": [{"type": "text", "text": "Você é o Claude Code."}],
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Liste os arquivos."}]},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Vou listar."},
                            {
                                "type": "tool_use",
                                "id": "toolu_claude_1",
                                "name": "list_files",
                                "input": {"path": "."},
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_claude_1",
                                "content": "README.md\ndocs/",
                            }
                        ],
                    },
                ],
                "tools": TOOLS,
            },
        )

    assert second.status_code == 200
    second_events = events(second)
    text = "".join(
        str(payload["delta"]["text"])
        for name, payload in second_events
        if name == "content_block_delta" and payload["delta"]["type"] == "text_delta"  # type: ignore[index]
    )
    assert text == "Encontrei 2 arquivos."
    final_delta = next(payload for name, payload in second_events if name == "message_delta")
    assert final_delta["delta"]["stop_reason"] == "end_turn"
    assert second_events[-1][0] == "message_stop"

    recorded = provider.calls[-1]
    assert recorded.streamed is True
    assert recorded.messages[0].role == "system"
    assert recorded.messages[0].content == "Você é o Claude Code."
    assert recorded.messages[-1].role == "tool"
    assert recorded.messages[-1].tool_call_id == "toolu_claude_1"
    assert recorded.messages[-1].content == "README.md\ndocs/"
    assert recorded.tools is not None
    assert recorded.tools[0].name == "list_files"
    assert recorded.tool_choice is None

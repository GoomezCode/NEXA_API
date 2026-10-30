"""Fluxo E2E de um cliente Ollama contra o provedor fake (F16, CA-37).

Reproduz o ciclo que um cliente faz contra um servidor Ollama:

1. `GET /api/tags` para descobrir os modelos publicados;
2. `POST /api/show` para inspecionar capacidades;
3. `POST /api/chat` em NDJSON com `tools`, devolvendo o `tool_calls`;
4. nova chamada com `role=tool` e `tool_name`, recebendo a resposta final.
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

API_KEY = "nxa_test_ollama_client_01"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Lista arquivos de um diretório",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


@contextmanager
def ollama_client(provider: FakeProvider) -> Iterator[TestClient]:
    settings = make_settings(compat_api_keys=API_KEY)
    clock = FakeClock(datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
    with TestClient(create_app(settings, clock=clock, provider=provider)) as client:
        yield client


def lines(response: object) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in response.text.splitlines()  # type: ignore[attr-defined]
        if line.strip()
    ]


def test_ollama_client_tool_round_trip(provider: FakeProvider) -> None:
    provider.tool_calls = (ToolCall(id="call_1", name="list_files", arguments={"path": "."}),)
    provider.finish_reason = "tool_use"

    with ollama_client(provider) as client:
        tags = client.get("/api/tags", headers=AUTH)
        assert tags.status_code == 200
        model = next(item for item in tags.json()["models"] if item["name"] == "fake/fake-model")

        shown = client.post("/api/show", headers=AUTH, json={"model": model["name"]})
        assert shown.status_code == 200
        assert "tools" in shown.json()["capabilities"]

        first = client.post(
            "/api/chat",
            headers=AUTH,
            json={
                "model": model["name"],
                "messages": [{"role": "user", "content": "Liste os arquivos."}],
                "tools": TOOLS,
            },
        )
        assert first.status_code == 200
        first_lines = lines(first)
        assert first_lines[-1]["done"] is True
        assert first_lines[-1]["done_reason"] == "tool_calls"
        assert first_lines[-1]["message"]["tool_calls"] == [
            {"function": {"name": "list_files", "arguments": {"path": "."}}}
        ]

        provider.tool_calls = ()
        provider.finish_reason = "stop"
        provider.chunks = ("Encontrei", " 3 arquivos.")

        second = client.post(
            "/api/chat",
            headers=AUTH,
            json={
                "model": model["name"],
                "messages": [
                    {"role": "user", "content": "Liste os arquivos."},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "list_files", "arguments": {"path": "."}}}
                        ],
                    },
                    {"role": "tool", "tool_name": "list_files", "content": "a.py b.py c.py"},
                ],
                "tools": TOOLS,
                "stream": False,
            },
        )
        assert second.status_code == 200
        body = second.json()
        assert body["done"] is True
        assert body["message"]["content"] == "Encontrei 3 arquivos."

    assert provider.calls[0].tools is not None
    assert provider.calls[0].tools[0].name == "list_files"
    assert provider.calls[1].messages[-1].role == "tool"
    assert provider.calls[1].messages[-1].content == "a.py b.py c.py"

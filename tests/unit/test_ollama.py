"""Testes do OllamaProvider com respx (F2-02)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from app.ai.base import ChatMessage, ProviderError, ProviderTimeout, ProviderUnavailable
from app.ai.ollama import OllamaProvider, map_finish_reason
from tests.conftest import make_settings

CHAT_URL = "http://ollama.test/api/chat"
TAGS_URL = "http://ollama.test/api/tags"
MESSAGES = [
    ChatMessage(role="system", content="seja breve"),
    ChatMessage(role="user", content="oi"),
]


def make_provider(**overrides: object) -> OllamaProvider:
    settings = make_settings(ollama_url="http://ollama.test", ollama_model="llama3.1", **overrides)
    return OllamaProvider(settings)


@respx.mock
async def test_generate_success_sends_payload_and_maps_tokens() -> None:
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "olá!"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 12,
                "eval_count": 5,
            },
        )
    )

    result = await make_provider().generate(MESSAGES, max_tokens=64, max_seconds=10)

    assert result.content == "olá!"
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 5

    request = route.calls[0].request
    payload = json.loads(request.content)
    assert payload == {
        "model": "llama3.1",
        "messages": [
            {"role": "system", "content": "seja breve"},
            {"role": "user", "content": "oi"},
        ],
        "stream": False,
        "options": {"num_predict": 64},
    }


@respx.mock
async def test_generate_forwards_request_id_header() -> None:
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"message": {"content": "ok"}})
    )

    await make_provider().generate(MESSAGES, max_tokens=10, max_seconds=10, request_id="req-123")

    assert route.calls[0].request.headers["x-request-id"] == "req-123"


@respx.mock
async def test_generate_without_request_id_omits_header() -> None:
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"message": {"content": "ok"}})
    )

    await make_provider().generate(MESSAGES, max_tokens=10, max_seconds=10)

    assert "x-request-id" not in route.calls[0].request.headers


@pytest.mark.parametrize("status_code", [400, 500, 503])
@respx.mock
async def test_generate_maps_http_errors(status_code: int) -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(status_code, text="falhou"))

    with pytest.raises(ProviderError) as exc_info:
        await make_provider().generate(MESSAGES, max_tokens=10, max_seconds=10)
    assert not isinstance(exc_info.value, ProviderUnavailable)
    assert str(status_code) in str(exc_info.value)


@respx.mock
async def test_generate_maps_connect_errors() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("recusado"))

    with pytest.raises(ProviderUnavailable):
        await make_provider().generate(MESSAGES, max_tokens=10, max_seconds=10)


@respx.mock
async def test_generate_maps_read_timeout() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("lento"))

    with pytest.raises(ProviderTimeout):
        await make_provider().generate(MESSAGES, max_tokens=10, max_seconds=10)


@respx.mock
async def test_generate_total_deadline_finishes_with_timeout_reason() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"message": {"content": "tarde"}})

    respx.post(CHAT_URL).mock(side_effect=slow)

    result = await make_provider(ai_timeout_seconds=0.05).generate(
        MESSAGES, max_tokens=10, max_seconds=0.05
    )

    assert result.content == ""
    assert result.finish_reason == "timeout"


@respx.mock
async def test_generate_rejects_invalid_json() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text="não é json"))

    with pytest.raises(ProviderError):
        await make_provider().generate(MESSAGES, max_tokens=10, max_seconds=10)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"done": True},
        {"message": "texto"},
        {"message": {"content": None}},
    ],
)
@respx.mock
async def test_generate_rejects_invalid_shapes(payload: object) -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(ProviderError):
        await make_provider().generate(MESSAGES, max_tokens=10, max_seconds=10)


def test_maps_done_reason() -> None:
    assert map_finish_reason("length") == "length"
    assert map_finish_reason("timeout") == "timeout"
    assert map_finish_reason("stop") == "stop"
    assert map_finish_reason(None) == "stop"
    assert map_finish_reason("desconhecido") == "stop"


async def _collect(provider: OllamaProvider) -> list[str]:
    return [
        chunk.content async for chunk in provider.stream(MESSAGES, max_tokens=32, max_seconds=10)
    ]


@respx.mock
async def test_stream_yields_content_then_final_chunk() -> None:
    body = (
        b'{"message":{"content":"Ol"},"done":false}\n'
        b"\n"
        b'{"message":{"content":"a"},"done":false}\n'
        b'{"message":{"content":"!"},"done":true,"done_reason":"length",'
        b'"prompt_eval_count":9,"eval_count":3}\n'
    )
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, content=body))

    chunks = [
        chunk
        async for chunk in make_provider().stream(
            MESSAGES, max_tokens=32, max_seconds=10, request_id="req-stream"
        )
    ]

    assert [chunk.content for chunk in chunks] == ["Ol", "a", "!"]
    assert [chunk.finish_reason for chunk in chunks] == [None, None, "length"]
    assert chunks[-1].done is True
    assert chunks[-1].prompt_tokens == 9
    assert chunks[-1].completion_tokens == 3
    assert route.calls[0].request.headers["x-request-id"] == "req-stream"

    payload = json.loads(route.calls[0].request.content)
    assert payload["stream"] is True
    assert payload["options"] == {"num_predict": 32}


@respx.mock
async def test_stream_final_chunk_without_content() -> None:
    body = (
        b'{"message":{"content":"parcial"},"done":false}\n'
        b'{"message":{"content":""},"done":true,"done_reason":"stop"}\n'
    )
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, content=body))

    chunks = await _collect(make_provider())

    assert chunks == ["parcial", ""]


@respx.mock
async def test_stream_raises_when_truncated() -> None:
    body = b'{"message":{"content":"a"},"done":false}\n'
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, content=body))

    with pytest.raises(ProviderError):
        await _collect(make_provider())


@respx.mock
async def test_stream_raises_on_error_line() -> None:
    body = b'{"error":"modelo nao encontrado"}\n'
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, content=body))

    with pytest.raises(ProviderError):
        await _collect(make_provider())


@pytest.mark.parametrize("line", [b"nao-json\n", b"[]\n", b'"texto"\n'])
@respx.mock
async def test_stream_rejects_invalid_lines(line: bytes) -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, content=line))

    with pytest.raises(ProviderError):
        await _collect(make_provider())


@respx.mock
async def test_stream_maps_http_error() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="erro"))

    with pytest.raises(ProviderError):
        await _collect(make_provider())


@respx.mock
async def test_stream_maps_connect_error() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("recusado"))

    with pytest.raises(ProviderUnavailable):
        await _collect(make_provider())


@respx.mock
async def test_stream_maps_read_timeout() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("lento"))

    with pytest.raises(ProviderTimeout):
        await _collect(make_provider())


@respx.mock
async def test_stream_total_deadline_ends_with_timeout_reason() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, content=b'{"done":true}\n')

    respx.post(CHAT_URL).mock(side_effect=slow)
    provider = make_provider(ai_timeout_seconds=0.05)

    chunks = [chunk async for chunk in provider.stream(MESSAGES, max_tokens=10, max_seconds=0.05)]

    assert len(chunks) == 1
    assert chunks[0].finish_reason == "timeout"
    assert chunks[0].content == ""


@respx.mock
async def test_health_returns_true_on_200() -> None:
    respx.get(TAGS_URL).mock(return_value=httpx.Response(200, json={"models": []}))
    assert await make_provider().health() is True


@respx.mock
async def test_health_returns_false_on_error_status() -> None:
    respx.get(TAGS_URL).mock(return_value=httpx.Response(503))
    assert await make_provider().health() is False


@respx.mock
async def test_health_returns_false_on_connection_error() -> None:
    respx.get(TAGS_URL).mock(side_effect=httpx.ConnectError("recusado"))
    assert await make_provider().health() is False


@respx.mock
async def test_health_returns_false_on_timeout() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200)

    respx.get(TAGS_URL).mock(side_effect=slow)
    assert await make_provider(ai_timeout_seconds=0.01).health() is False


def test_provider_exposes_name_and_model() -> None:
    provider = make_provider()
    assert provider.name == "ollama"
    assert provider.model == "llama3.1"


@respx.mock
async def test_list_models_parses_tags_and_sorts_by_name() -> None:
    respx.get(TAGS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "zeta:latest",
                        "size": 0,
                        "modified_at": "2026-10-28T14:30:00.123456789Z",
                        "details": {
                            "family": "zeta",
                            "parameter_size": "0.5B",
                            "quantization_level": "Q8_0",
                        },
                    },
                    {"name": "alpha:latest", "size": 42},
                    {"name": 10},
                    {"size": 1},
                    "ignorado",
                ]
            },
        )
    )

    models = await make_provider().list_models(request_id="req-tags")

    assert [model.name for model in models] == ["alpha:latest", "zeta:latest"]
    assert models[0].size_bytes == 42
    assert models[0].modified_at is None
    assert models[0].family is None
    assert models[1].size_bytes == 0
    assert models[1].family == "zeta"
    assert models[1].parameter_size == "0.5B"
    assert models[1].quantization_level == "Q8_0"
    assert models[1].modified_at is not None
    assert models[1].modified_at.isoformat() == "2026-10-28T14:30:00.123456+00:00"

    assert respx.calls.last.request.headers["x-request-id"] == "req-tags"


@respx.mock
async def test_list_models_accepts_naive_dates_and_rejects_invalid_ones() -> None:
    respx.get(TAGS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"name": "sem-fuso", "modified_at": "2026-10-28T10:00:00"},
                    {"name": "invalida", "modified_at": "ontem"},
                    {"name": "sem-tipo", "modified_at": 123},
                ]
            },
        )
    )

    models = await make_provider().list_models()

    by_name = {model.name: model for model in models}
    assert by_name["sem-fuso"].modified_at is not None
    assert by_name["sem-fuso"].modified_at.tzinfo is not None
    assert by_name["invalida"].modified_at is None
    assert by_name["sem-tipo"].modified_at is None


@pytest.mark.parametrize(
    "payload",
    [
        {"models": "x"},
        {"fundamentals": []},
        [],
    ],
)
@respx.mock
async def test_list_models_rejects_invalid_shapes(payload: object) -> None:
    respx.get(TAGS_URL).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(ProviderError):
        await make_provider().list_models()


@respx.mock
async def test_list_models_rejects_invalid_json() -> None:
    respx.get(TAGS_URL).mock(return_value=httpx.Response(200, text="não é json"))

    with pytest.raises(ProviderError):
        await make_provider().list_models()


@respx.mock
async def test_list_models_maps_http_error() -> None:
    respx.get(TAGS_URL).mock(return_value=httpx.Response(503, text="fora"))

    with pytest.raises(ProviderError) as exc_info:
        await make_provider().list_models()
    assert not isinstance(exc_info.value, ProviderUnavailable)
    assert "503" in str(exc_info.value)


@respx.mock
async def test_list_models_maps_connect_error() -> None:
    respx.get(TAGS_URL).mock(side_effect=httpx.ConnectError("recusado"))

    with pytest.raises(ProviderUnavailable):
        await make_provider().list_models()


@respx.mock
async def test_list_models_maps_read_timeout() -> None:
    respx.get(TAGS_URL).mock(side_effect=httpx.ReadTimeout("lento"))

    with pytest.raises(ProviderTimeout):
        await make_provider().list_models()


@respx.mock
async def test_list_models_maps_total_deadline() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"models": []})

    respx.get(TAGS_URL).mock(side_effect=slow)

    with pytest.raises(ProviderTimeout):
        await make_provider(ai_timeout_seconds=0.01).list_models()


@respx.mock
async def test_generate_uses_request_model() -> None:
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"message": {"content": "ok"}})
    )

    await make_provider().generate(MESSAGES, max_tokens=10, max_seconds=10, model="outro:7b")

    payload = json.loads(route.calls[0].request.content)
    assert payload["model"] == "outro:7b"


@respx.mock
async def test_stream_uses_request_model() -> None:
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, content=b'{"message":{"content":"ok"},"done":true}\n')
    )

    _ = [
        chunk
        async for chunk in make_provider().stream(
            MESSAGES, max_tokens=10, max_seconds=10, model="outro:7b"
        )
    ]

    payload = json.loads(route.calls[0].request.content)
    assert payload["model"] == "outro:7b"

"""Testes do provedor fake e do registro na factory (CA-15)."""

from __future__ import annotations

import pytest

from app.ai.base import (
    ChatMessage,
    ProviderConfigurationError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.ai.factory import (
    _REGISTRY,
    available_providers,
    build_provider,
    register_provider,
)
from app.ai.fake import FakeProvider
from app.ai.ollama import OllamaProvider
from app.main import create_app
from tests.conftest import FakeClock, make_settings

MESSAGES = [ChatMessage(role="user", content="oi")]


async def test_generate_returns_deterministic_result() -> None:
    provider = FakeProvider(chunks=("Olá", ", mundo"), completion_tokens=4)

    result = await provider.generate(MESSAGES, max_tokens=128, max_seconds=5, request_id="req-1")

    assert result.content == "Olá, mundo"
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 4

    call = provider.calls[0]
    assert call.messages == tuple(MESSAGES)
    assert (call.max_tokens, call.max_seconds) == (128, 5)
    assert call.request_id == "req-1"
    assert call.streamed is False


async def test_stream_yields_content_and_final_chunk() -> None:
    provider = FakeProvider(chunks=("a", "b"), finish_reason="length")

    chunks = [chunk async for chunk in provider.stream(MESSAGES, max_tokens=8, max_seconds=2)]

    assert [chunk.content for chunk in chunks] == ["a", "b", ""]
    assert [chunk.finish_reason for chunk in chunks] == [None, None, "length"]
    assert chunks[-1].completion_tokens == 7
    assert provider.calls[0].streamed is True


async def test_generate_raises_configured_error() -> None:
    provider = FakeProvider(error="timeout")

    with pytest.raises(ProviderTimeout):
        await provider.generate(MESSAGES, max_tokens=8, max_seconds=2)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("unavailable", ProviderUnavailable),
        ("error", ProviderError),
        ("qualquer coisa", ProviderError),
    ],
)
async def test_error_shortcuts(error: str, expected: type[Exception]) -> None:
    provider = FakeProvider(error=error)

    with pytest.raises(expected):
        await provider.generate(MESSAGES, max_tokens=8, max_seconds=2)


async def test_generate_accepts_exception_instance() -> None:
    provider = FakeProvider(error=ProviderUnavailable("fora do ar"))

    with pytest.raises(ProviderUnavailable):
        await provider.generate(MESSAGES, max_tokens=8, max_seconds=2)


async def test_stream_fails_before_first_chunk_by_default() -> None:
    provider = FakeProvider(error="error")

    seen: list[str] = []
    with pytest.raises(ProviderError):
        async for chunk in provider.stream(MESSAGES, max_tokens=8, max_seconds=2):
            seen.append(chunk.content)

    assert seen == []


async def test_stream_fails_after_configured_chunks() -> None:
    provider = FakeProvider(chunks=("a", "b", "c"), error="timeout", error_after_chunks=2)

    seen: list[str] = []
    with pytest.raises(ProviderTimeout):
        async for chunk in provider.stream(MESSAGES, max_tokens=8, max_seconds=2):
            seen.append(chunk.content)

    assert seen == ["a", "b"]


async def test_stream_fails_after_last_chunk() -> None:
    provider = FakeProvider(chunks=("a",), error="error", error_after_chunks=1)

    seen: list[str] = []
    with pytest.raises(ProviderError):
        async for chunk in provider.stream(MESSAGES, max_tokens=8, max_seconds=2):
            seen.append(chunk.content)

    assert seen == ["a"]


async def test_delay_does_not_change_result() -> None:
    provider = FakeProvider(chunks=("lento",), delay=0.001)

    result = await provider.generate(MESSAGES, max_tokens=8, max_seconds=2)
    streamed = [chunk async for chunk in provider.stream(MESSAGES, max_tokens=8, max_seconds=2)]

    assert result.content == "lento"
    assert streamed[0].content == "lento"


async def test_health_can_be_healthy_unhealthy_or_raise() -> None:
    assert await FakeProvider().health() is True
    assert await FakeProvider(healthy=False).health() is False

    with pytest.raises(RuntimeError):
        await FakeProvider(health_error=RuntimeError("falhou")).health()


def test_fake_provider_is_not_configurable_by_env() -> None:
    # `AI_PROVIDER` só aceita ollama|cloud; o fake é injetado/buildado por nome.
    assert make_settings().ai_provider == "ollama"


def test_factory_builds_default_ollama_provider() -> None:
    provider = build_provider(make_settings())

    assert isinstance(provider, OllamaProvider)
    assert provider.name == "ollama"
    assert provider.model == "llama3.1"


def test_factory_builds_fake_by_name() -> None:
    provider = build_provider(make_settings(), name="fake")

    assert isinstance(provider, FakeProvider)
    assert provider.name == "fake"
    assert provider.model == "fake-model"


def test_factory_rejects_cloud_with_clear_error() -> None:
    settings = make_settings(
        ai_provider="cloud",
        cloud_provider="openai",
        cloud_model="gpt-4o-mini",
    )

    with pytest.raises(ProviderConfigurationError) as exc_info:
        build_provider(settings)
    assert "cloud" in str(exc_info.value)
    assert "ollama" in str(exc_info.value)


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ProviderConfigurationError) as exc_info:
        build_provider(make_settings(), name="inexistente")
    assert "inexistente" in str(exc_info.value)


def test_available_providers_are_sorted() -> None:
    providers = available_providers()
    assert providers == tuple(sorted(providers))
    assert {"ollama", "cloud", "fake"} <= set(providers)


def test_register_custom_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.ai.factory._REGISTRY", dict(_REGISTRY))
    register_provider("custom", lambda settings: FakeProvider(model="custom-model"))

    provider = build_provider(make_settings(), name="custom")

    assert provider.model == "custom-model"
    assert "custom" in available_providers()


def test_register_rejects_duplicates_and_empty_names() -> None:
    with pytest.raises(ValueError):
        register_provider("ollama", lambda settings: FakeProvider())
    with pytest.raises(ValueError):
        register_provider("", lambda settings: FakeProvider())


def test_register_can_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.ai.factory._REGISTRY", dict(_REGISTRY))
    register_provider("fake", lambda settings: FakeProvider(model="outro"), replace=True)

    assert build_provider(make_settings(), name="fake").model == "outro"


def test_factory_rejects_provider_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.ai.factory._REGISTRY", dict(_REGISTRY))
    register_provider("sem-modelo", lambda settings: FakeProvider(model=""))

    with pytest.raises(ProviderConfigurationError):
        build_provider(make_settings(), name="sem-modelo")


def test_create_app_uses_injected_provider() -> None:
    provider = FakeProvider(model="injetado", healthy=False)
    app = create_app(make_settings(), clock=FakeClock(), provider=provider)

    assert app.state.ai_provider is provider
    assert app.state.ai_provider_name == "fake"
    assert app.state.ai_model == "injetado"
    assert app.state.ai_health == provider.health


def test_create_app_builds_provider_from_settings() -> None:
    app = create_app(make_settings(), clock=FakeClock())

    assert isinstance(app.state.ai_provider, OllamaProvider)
    assert app.state.ai_provider_name == "ollama"


def test_ca15_swap_provider_without_changing_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-15: trocar o provedor não exige alteração nas rotas."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr("app.ai.factory._REGISTRY", dict(_REGISTRY))

    class CustomProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__(model="modelo-ca15")
            self.name = "custom-ca15"

    register_provider("custom-ca15", lambda settings: CustomProvider())
    settings = make_settings()
    provider = build_provider(settings, name="custom-ca15")
    app = create_app(settings, clock=FakeClock(), provider=provider)

    with TestClient(app) as client:
        ready = client.get("/health/ready")
        health = client.get("/health")

    assert ready.status_code == 200
    assert ready.json()["provider"] == "custom-ca15"
    assert app.state.ai_model == "modelo-ca15"
    assert health.status_code == 200


def test_create_app_fails_fast_for_cloud() -> None:
    settings = make_settings(
        ai_provider="cloud", cloud_provider="openai", cloud_model="gpt-4o-mini"
    )

    with pytest.raises(ProviderConfigurationError):
        create_app(settings, clock=FakeClock())

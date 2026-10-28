"""Testes do esqueleto do provedor de nuvem (fora do escopo, §5.1)."""

from __future__ import annotations

import pytest

from app.ai.base import ChatMessage, ProviderConfigurationError
from app.ai.cloud import CloudProvider
from tests.conftest import make_settings

MESSAGES = [ChatMessage(role="user", content="oi")]


def _provider() -> CloudProvider:
    settings = make_settings(
        ai_provider="cloud", cloud_provider="openai", cloud_model="gpt-4o-mini"
    )
    return CloudProvider(settings)


def test_exposes_name_and_configured_model() -> None:
    provider = _provider()
    assert provider.name == "cloud"
    assert provider.model == "gpt-4o-mini"


def test_model_defaults_to_empty_without_configuration() -> None:
    provider = CloudProvider(make_settings())
    assert provider.model == ""


async def test_generate_is_not_implemented() -> None:
    with pytest.raises(ProviderConfigurationError):
        await _provider().generate(MESSAGES, max_tokens=10, max_seconds=5)


async def test_stream_is_not_implemented() -> None:
    with pytest.raises(ProviderConfigurationError):
        async for _ in _provider().stream(MESSAGES, max_tokens=10, max_seconds=5):
            pass


async def test_health_is_not_implemented() -> None:
    with pytest.raises(ProviderConfigurationError):
        await _provider().health()

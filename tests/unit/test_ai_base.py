"""Testes do contrato dos provedores de IA (base.py)."""

from __future__ import annotations

import pytest

from app.ai.base import (
    ChatMessage,
    GenerationChunk,
    GenerationResult,
    ProviderConfigurationError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)


def test_chat_message_is_frozen() -> None:
    message = ChatMessage(role="user", content="oi")
    with pytest.raises(AttributeError):
        message.content = "outro"  # type: ignore[misc]


def test_generation_chunk_done_flag() -> None:
    assert GenerationChunk(content="parcial").done is False
    final = GenerationChunk(content="fim", finish_reason="length", completion_tokens=4)
    assert final.done is True
    assert final.finish_reason == "length"
    assert final.completion_tokens == 4


def test_generation_result_fields() -> None:
    result = GenerationResult(
        content="olá",
        finish_reason="stop",
        prompt_tokens=3,
        completion_tokens=2,
    )
    assert result.content == "olá"
    assert result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens) == (3, 2)


def test_error_hierarchy() -> None:
    assert issubclass(ProviderUnavailable, ProviderError)
    assert issubclass(ProviderTimeout, ProviderError)
    assert issubclass(ProviderConfigurationError, ProviderError)
    assert issubclass(ProviderConfigurationError, ValueError)

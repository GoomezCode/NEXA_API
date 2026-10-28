"""Contrato dos provedores de IA (RF-CHAT-05, RNF-MAN-01)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

FinishReason = Literal["stop", "length", "timeout"]
MessageRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Mensagem no formato comum aceito por todos os provedores."""

    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Resposta completa de uma geração."""

    content: str
    finish_reason: FinishReason
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Modelo instalado no provedor, como exibido ao cliente."""

    name: str
    size_bytes: int | None = None
    modified_at: datetime | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationChunk:
    """Trecho incremental; o último trecho traz `finish_reason`."""

    content: str = ""
    finish_reason: FinishReason | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def done(self) -> bool:
        return self.finish_reason is not None


class ProviderError(Exception):
    """Falha genérica do provedor de IA (→ 502)."""


class ProviderUnavailable(ProviderError):
    """Provedor inacessível (conexão recusada, DNS, etc.)."""


class ProviderTimeout(ProviderError):
    """Provedor não respondeu dentro do limite (→ 504)."""


class ProviderConfigurationError(ProviderError, ValueError):
    """Configuração do provedor inválida ou recurso não implementado."""


def provider_error_code(exc: BaseException) -> str:
    """Código Problem Details para falhas de catálogo/validação de modelo."""
    if isinstance(exc, ProviderTimeout):
        return "ai_provider_timeout"
    if isinstance(exc, ProviderUnavailable):
        return "provider_unavailable"
    return "ai_provider_error"


class AIProvider(Protocol):
    """Interface Strategy para provedores de IA (RNF-MAN-01/02)."""

    name: str
    model: str

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
    ) -> GenerationResult: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[GenerationChunk]: ...

    async def health(self) -> bool: ...

    async def list_models(self, *, request_id: str | None = None) -> Sequence[ModelInfo]: ...


__all__ = [
    "AIProvider",
    "ChatMessage",
    "FinishReason",
    "GenerationChunk",
    "GenerationResult",
    "MessageRole",
    "ModelInfo",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "provider_error_code",
]

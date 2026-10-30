"""Contrato dos provedores de IA (RF-GTW-01, RNF-MAN-01).

Toda a comunicação com IA passa por `AIProvider`. O `ProviderRouter` resolve o
Model ID (`provider/model`), aplica aliases/allowlist, valida capacidades e
escolhe o adapter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

FinishReason = Literal["stop", "length", "timeout", "tool_use"]
MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Ferramenta declarada pelo cliente, no formato interno."""

    name: str
    description: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Chamada de ferramenta produzida pelo modelo."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Mensagem no formato comum aceito por todos os provedores."""

    role: MessageRole
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Capacidades declaradas por um adapter/modelo (RF-GTW-06)."""

    chat: bool = True
    stream: bool = True
    tools: bool = False
    vision: bool = False
    json_mode: bool = False
    context_window: int | None = None

    def supports(self, capability: str) -> bool:
        """`True` quando a capacidade é conhecida e habilitada."""
        if capability == "chat":
            return self.chat
        if capability == "stream":
            return self.stream
        if capability == "tools":
            return self.tools
        if capability == "vision":
            return self.vision
        if capability == "json_mode":
            return self.json_mode
        return False


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Resposta completa de uma geração."""

    content: str
    finish_reason: FinishReason
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Modelo disponível no provedor, como exibido ao cliente."""

    name: str
    size_bytes: int | None = None
    modified_at: datetime | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    capabilities: Capabilities | None = None
    context_window: int | None = None


@dataclass(frozen=True, slots=True)
class GenerationChunk:
    """Trecho incremental; o último trecho traz `finish_reason`."""

    content: str = ""
    finish_reason: FinishReason | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tool_calls: tuple[ToolCall, ...] = ()

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


class GatewayError(Exception):
    """Erro de roteamento/validação do gateway (→ 422 por padrão)."""

    code = "gateway_error"
    status_code = 422


class ModelNotFound(GatewayError):
    """Model ID desconhecido em todos os provedores habilitados."""

    code = "model_not_found"


class ModelNotAllowed(GatewayError):
    """Modelo fora de `AI_ALLOWED_MODELS`."""

    code = "model_not_allowed"


class UnsupportedCapability(GatewayError):
    """Modelo/provedor não suporta a capacidade exigida."""

    code = "unsupported_capability"

    def __init__(self, message: str, *, capability: str | None = None) -> None:
        super().__init__(message)
        self.capability = capability


def provider_error_code(exc: BaseException) -> str:
    """Código Problem Details para falhas de catálogo/validação de modelo."""
    if isinstance(exc, GatewayError):
        return exc.code
    if isinstance(exc, ProviderTimeout):
        return "ai_provider_timeout"
    if isinstance(exc, ProviderUnavailable):
        return "provider_unavailable"
    return "ai_provider_error"


class AIProvider(Protocol):
    """Interface Strategy para provedores de IA (RNF-MAN-01/02)."""

    name: str
    model: str
    capabilities: Capabilities

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> GenerationResult: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[GenerationChunk]: ...

    async def health(self) -> bool: ...

    async def list_models(self, *, request_id: str | None = None) -> Sequence[ModelInfo]: ...

    async def count_tokens(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
    ) -> int | None: ...


__all__ = [
    "AIProvider",
    "Capabilities",
    "ChatMessage",
    "FinishReason",
    "GatewayError",
    "GenerationChunk",
    "GenerationResult",
    "MessageRole",
    "ModelInfo",
    "ModelNotAllowed",
    "ModelNotFound",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "ToolCall",
    "ToolSpec",
    "UnsupportedCapability",
    "provider_error_code",
]

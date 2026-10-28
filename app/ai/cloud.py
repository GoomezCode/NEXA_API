"""Esqueleto do provedor de nuvem (fora do escopo da v1.1, §5.1).

O contrato existe para que a factory e as rotas não precisem mudar quando o
`CloudProvider` real for implementado. Qualquer tentativa de uso falha com
`ProviderConfigurationError`, sem expor chaves ou detalhes internos.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.ai.base import (
    ChatMessage,
    GenerationChunk,
    GenerationResult,
    ModelInfo,
    ProviderConfigurationError,
)
from app.config import Settings

_MESSAGE = (
    "O provedor 'cloud' não está implementado nesta versão; "
    "use AI_PROVIDER=ollama ou registre um provedor na factory."
)


class CloudProvider:
    """Esqueleto do `AIProvider` para provedores de nuvem."""

    name = "cloud"
    model: str

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.model = settings.cloud_model or ""

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
    ) -> GenerationResult:
        raise ProviderConfigurationError(_MESSAGE)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[GenerationChunk]:
        raise ProviderConfigurationError(_MESSAGE)
        yield GenerationChunk()  # pragma: no cover - torna o método um gerador

    async def health(self) -> bool:
        raise ProviderConfigurationError(_MESSAGE)

    async def list_models(self, *, request_id: str | None = None) -> Sequence[ModelInfo]:
        raise ProviderConfigurationError(_MESSAGE)


__all__ = ["CloudProvider"]

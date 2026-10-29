"""Catálogo de modelos do provedor com cache curto e resolução de nomes.

O catálogo evita uma chamada a `GET /api/tags` por mensagem de chat e concentra
a regra de nomes do Ollama (`modelo` ↔ `modelo:latest`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime

from app.ai.base import AIProvider, ModelInfo
from app.core.clock import Clock

DEFAULT_CACHE_SECONDS = 10.0


def _has_tag(name: str) -> bool:
    """Considera a tag depois da última `/`, para nomes com registro/namespace."""
    return ":" in name[name.rfind("/") + 1 :]


def _same_model(left: str, right: str) -> bool:
    return left == right or f"{left}:latest" == right or left == f"{right}:latest"


def is_model_selectable(
    name: str,
    *,
    allowed: Sequence[str],
    default_model: str,
) -> bool:
    """O modelo pode ser escolhido explicitamente?

    Sem allowlist, todos os instalados podem. Com allowlist, apenas os listados
    (aceitando nome sem tag) ou o modelo padrão do servidor.
    """
    if not allowed or _same_model(name, default_model):
        return True
    return any(_same_model(name, entry) for entry in allowed)


class ModelCatalog:
    """Lista e resolve modelos instalados, com cache TTL opcional."""

    def __init__(
        self,
        provider: AIProvider,
        clock: Clock,
        *,
        ttl_seconds: float = DEFAULT_CACHE_SECONDS,
    ) -> None:
        self._provider = provider
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._cached: tuple[ModelInfo, ...] | None = None
        self._cached_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def list_models(
        self,
        *,
        refresh: bool = False,
        request_id: str | None = None,
    ) -> list[ModelInfo]:
        """Modelos instalados; usa o cache, exceto com `refresh` ou TTL zero."""
        if not refresh and (cached := self._fresh()) is not None:
            return list(cached)

        async with self._lock:
            if not refresh and (cached := self._fresh()) is not None:
                return list(cached)
            models = tuple(await self._provider.list_models(request_id=request_id))
            self._cached = models
            self._cached_at = self._clock.now()
            return list(models)

    async def resolve(
        self,
        requested: str,
        *,
        request_id: str | None = None,
    ) -> str | None:
        """Devolve o nome canônico instalado ou `None` se não existir."""
        model = await self.resolve_with_info(requested, request_id=request_id)
        return model.name if model is not None else None

    async def resolve_with_info(
        self,
        requested: str,
        *,
        request_id: str | None = None,
    ) -> ModelInfo | None:
        """Devolve o modelo instalado (com metadados) ou `None`."""
        models = await self.list_models(request_id=request_id)
        if requested in {model.name for model in models}:
            for model in models:
                if model.name == requested:
                    return model
        if not _has_tag(requested):
            candidate = f"{requested}:latest"
            for model in models:
                if model.name == candidate:
                    return model
        return None

    def _fresh(self) -> tuple[ModelInfo, ...] | None:
        if self._ttl_seconds <= 0 or self._cached is None or self._cached_at is None:
            return None
        age = (self._clock.now() - self._cached_at).total_seconds()
        return self._cached if age < self._ttl_seconds else None


__all__ = [
    "DEFAULT_CACHE_SECONDS",
    "ModelCatalog",
    "is_model_selectable",
]

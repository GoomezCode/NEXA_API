"""Factory de provedores: `AI_PROVIDER` → implementação concreta (RF-CHAT-05)."""

from __future__ import annotations

from collections.abc import Callable

from app.ai.base import AIProvider, ProviderConfigurationError
from app.ai.cloud import CloudProvider
from app.ai.fake import FakeProvider
from app.ai.ollama import OllamaProvider
from app.config import Settings

ProviderBuilder = Callable[[Settings], AIProvider]

_CLOUD_MESSAGE = (
    "O provedor 'cloud' não está implementado nesta versão (SRS §5.1); use AI_PROVIDER=ollama."
)


def _build_ollama(settings: Settings) -> AIProvider:
    return OllamaProvider(settings)


def _build_fake(settings: Settings) -> AIProvider:
    return FakeProvider()


def _build_cloud(settings: Settings) -> AIProvider:
    # O esqueleto existe, mas o provedor real está fora do escopo da v1.1.
    raise ProviderConfigurationError(_CLOUD_MESSAGE)


_REGISTRY: dict[str, ProviderBuilder] = {
    "ollama": _build_ollama,
    "cloud": _build_cloud,
    "fake": _build_fake,
}


def register_provider(name: str, builder: ProviderBuilder, *, replace: bool = False) -> None:
    """Registra um provedor adicional (CA-15), sem alterar as rotas."""
    if not name:
        raise ValueError("nome do provedor não pode ser vazio")
    if name in _REGISTRY and not replace:
        raise ValueError(f"provedor {name!r} já registrado")
    _REGISTRY[name] = builder


def available_providers() -> tuple[str, ...]:
    """Nomes registrados, em ordem estável."""
    return tuple(sorted(_REGISTRY))


def build_provider(settings: Settings, *, name: str | None = None) -> AIProvider:
    """Cria o provedor configurado; falha cedo e com mensagem clara."""
    provider_name = name or settings.ai_provider
    builder = _REGISTRY.get(provider_name)
    if builder is None:
        raise ProviderConfigurationError(
            f"Provedor de IA desconhecido: {provider_name!r}. "
            f"Disponíveis: {', '.join(available_providers())}."
        )
    provider = builder(settings)
    if not provider.model:
        raise ProviderConfigurationError(f"O provedor {provider_name!r} não definiu um modelo.")
    return provider


__all__ = [
    "CloudProvider",
    "ProviderBuilder",
    "available_providers",
    "build_provider",
    "register_provider",
]

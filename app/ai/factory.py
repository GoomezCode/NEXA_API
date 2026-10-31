"""Registry de provedores de IA (RF-GTW-01/10, RNF-MAN-02).

O registry é o único ponto que conhece as classes concretas. Adicionar um
provedor exige: um adapter novo, uma entrada em `_REGISTRY` e as variáveis de
ambiente correspondentes.
"""

from __future__ import annotations

from collections.abc import Callable

from app.ai.anthropic import AnthropicProvider
from app.ai.base import AIProvider, ProviderConfigurationError
from app.ai.fake import FakeProvider
from app.ai.google import GoogleProvider
from app.ai.nvidia import NvidiaProvider
from app.ai.ollama import OllamaProvider
from app.ai.openai import OpenAIProvider
from app.ai.openai_compatible import OpenAICompatibleProvider
from app.config import KNOWN_PROVIDERS, Settings

ProviderBuilder = Callable[[Settings], AIProvider]

PROVIDER_LABELS: dict[str, str] = {
    "ollama": "Ollama local",
    "ollama_cloud": "Ollama Cloud",
    "openai": "OpenAI",
    "nvidia": "NVIDIA NIM",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "openai_compatible": "OpenAI-compatible",
    "fake": "Fake (testes)",
}


def _require(value: str | None, env_name: str, provider: str) -> str:
    if not value:
        raise ProviderConfigurationError(
            f"Provedor '{provider}' habilitado sem {env_name}. "
            "Defina a variável ou remova o provedor de AI_ENABLED_PROVIDERS."
        )
    return value


def _build_ollama(settings: Settings) -> AIProvider:
    return OllamaProvider(
        settings,
        name="ollama",
        base_url=settings.ollama_url,
        api_key=settings.ollama_api_key_value,
        default_model=settings.ollama_model,
    )


def _build_ollama_cloud(settings: Settings) -> AIProvider:
    api_key = _require(
        settings.ollama_cloud_api_key_value,
        "OLLAMA_CLOUD_API_KEY",
        "ollama_cloud",
    )
    return OllamaProvider(
        settings,
        name="ollama_cloud",
        base_url=settings.ollama_cloud_url,
        api_key=api_key,
        default_model=settings.ollama_cloud_default_model,
    )


def _build_openai(settings: Settings) -> AIProvider:
    api_key = _require(settings.openai_api_key_value, "OPENAI_API_KEY", "openai")
    return OpenAIProvider(
        settings,
        name="openai",
        base_url=settings.openai_base_url,
        api_key=api_key,
        default_model=settings.openai_default_model,
        organization=settings.openai_org_id,
        label="OpenAI",
    )


def _build_nvidia(settings: Settings) -> AIProvider:
    api_key = _require(settings.nvidia_api_key_value, "NVIDIA_API_KEY", "nvidia")
    return NvidiaProvider(
        settings,
        name="nvidia",
        base_url=settings.nvidia_base_url,
        api_key=api_key,
        default_model=settings.nvidia_default_model,
        label="NVIDIA NIM",
    )


def _build_anthropic(settings: Settings) -> AIProvider:
    _require(settings.anthropic_api_key_value, "ANTHROPIC_API_KEY", "anthropic")
    return AnthropicProvider(settings)


def _build_google(settings: Settings) -> AIProvider:
    _require(settings.google_api_key_value, "GOOGLE_API_KEY", "google")
    return GoogleProvider(settings)


def _build_openai_compatible(settings: Settings) -> AIProvider:
    _require(
        settings.openai_compat_base_url,
        "OPENAI_COMPAT_BASE_URL",
        "openai_compatible",
    )
    _require(
        settings.openai_compat_default_model,
        "OPENAI_COMPAT_DEFAULT_MODEL",
        "openai_compatible",
    )
    return OpenAICompatibleProvider(settings)


def _build_fake(settings: Settings) -> AIProvider:
    return FakeProvider()


def build_user_provider(
    settings: Settings,
    *,
    provider: str,
    api_key: str,
    base_url: str | None = None,
    default_model: str | None = None,
) -> AIProvider:
    """Constrói um adapter com credenciais fornecidas para uma única requisição."""
    if provider == "openai":
        return OpenAIProvider(
            settings,
            api_key=api_key,
            default_model=default_model,
            base_url=base_url or settings.openai_base_url,
            label="OpenAI",
        )
    if provider == "nvidia":
        return NvidiaProvider(
            settings,
            name="nvidia",
            api_key=api_key,
            base_url=base_url or settings.nvidia_base_url,
            default_model=default_model or settings.nvidia_default_model,
            label="NVIDIA NIM",
        )
    if provider == "anthropic":
        return AnthropicProvider(
            settings, api_key=api_key, base_url=base_url, default_model=default_model
        )
    if provider == "google":
        return GoogleProvider(
            settings, api_key=api_key, base_url=base_url, default_model=default_model
        )
    if provider == "ollama_cloud":
        return OllamaProvider(
            settings,
            name=provider,
            api_key=api_key,
            base_url=base_url or settings.ollama_cloud_url,
            default_model=default_model or settings.ollama_cloud_default_model,
        )
    if provider == "openai_compatible":
        if not base_url:
            raise ProviderConfigurationError("OpenAI-compatible exige base_url.")
        return OpenAICompatibleProvider(
            settings,
            api_key=api_key,
            base_url=base_url,
            default_model=default_model or "",
        )
    raise ProviderConfigurationError(f"Provedor BYOK não suportado: {provider}")


_REGISTRY: dict[str, ProviderBuilder] = {
    "ollama": _build_ollama,
    "ollama_cloud": _build_ollama_cloud,
    "openai": _build_openai,
    "nvidia": _build_nvidia,
    "anthropic": _build_anthropic,
    "google": _build_google,
    "openai_compatible": _build_openai_compatible,
    "fake": _build_fake,
}


def available_providers() -> tuple[str, ...]:
    """Nomes registrados, em ordem estável."""
    return tuple(sorted(_REGISTRY))


def provider_label(name: str) -> str:
    """Rótulo humano do provedor, sem expor detalhes internos."""
    return PROVIDER_LABELS.get(name, name)


def register_provider(
    name: str,
    builder: ProviderBuilder,
    *,
    replace: bool = False,
) -> None:
    """Registra um adapter novo; por padrão recusa sobrescrever (CA-15)."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("nome do provedor não pode ser vazio")
    if normalized in _REGISTRY and not replace:
        raise ValueError(f"provedor já registrado: {normalized}")
    _REGISTRY[normalized] = builder


def _expand_default(settings: Settings) -> str:
    value = settings.ai_model_aliases.get(
        settings.ai_default_model,
        settings.ai_default_model,
    )
    if "/" not in value:
        value = f"ollama/{value}"
    return value


def default_provider_name(settings: Settings) -> str:
    """Provedor do `AI_DEFAULT_MODEL` (após expandir alias)."""
    provider, _, model = _expand_default(settings).partition("/")
    if not model:
        raise ProviderConfigurationError("AI_DEFAULT_MODEL inválido")
    return provider


def enabled_provider_names(settings: Settings) -> tuple[str, ...]:
    """Provedores habilitados explícitos ou autodetectados por credencial."""
    if settings.ai_enabled_providers:
        return tuple(settings.ai_enabled_providers)

    names = ["ollama"]
    if settings.ollama_cloud_api_key_value:
        names.append("ollama_cloud")
    if settings.openai_api_key_value:
        names.append("openai")
    if settings.anthropic_api_key_value:
        names.append("anthropic")
    if settings.nvidia_api_key_value:
        names.append("nvidia")
    if settings.google_api_key_value:
        names.append("google")
    if settings.openai_compat_base_url:
        names.append("openai_compatible")
    return tuple(names)


def build_provider(settings: Settings, name: str | None = None) -> AIProvider:
    """Constrói um adapter registrado; `name=None` usa o provedor padrão."""
    resolved_name = name or default_provider_name(settings)
    if resolved_name not in KNOWN_PROVIDERS and resolved_name not in _REGISTRY:
        raise ProviderConfigurationError(
            f"Provedor desconhecido: '{resolved_name}'. "
            f"Disponíveis: {', '.join(available_providers())}."
        )
    builder = _REGISTRY.get(resolved_name)
    if builder is None:
        raise ProviderConfigurationError(
            f"Provedor '{resolved_name}' não está registrado. "
            f"Disponíveis: {', '.join(available_providers())}."
        )
    provider = builder(settings)
    if not provider.model:
        raise ProviderConfigurationError(
            f"Provedor '{resolved_name}' não tem modelo padrão configurado."
        )
    return provider


def build_registry(settings: Settings) -> dict[str, AIProvider]:
    """Constrói todos os provedores habilitados (RF-GTW-03)."""
    names = enabled_provider_names(settings)
    providers = {name: build_provider(settings, name) for name in names}

    default_name = default_provider_name(settings)
    if default_name not in providers:
        raise ProviderConfigurationError(
            f"O provedor padrão '{default_name}' (AI_DEFAULT_MODEL) não está habilitado. "
            f"Habilitados: {', '.join(sorted(providers))}."
        )
    return providers


__all__ = [
    "PROVIDER_LABELS",
    "ProviderBuilder",
    "available_providers",
    "build_provider",
    "build_registry",
    "default_provider_name",
    "enabled_provider_names",
    "provider_label",
    "register_provider",
]

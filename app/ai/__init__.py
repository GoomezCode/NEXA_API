"""Contrato e factory dos provedores de IA (SRS §3.1.2)."""

from app.ai.base import (
    AIProvider,
    ChatMessage,
    FinishReason,
    GenerationChunk,
    GenerationResult,
    MessageRole,
    ModelInfo,
    ProviderConfigurationError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    provider_error_code,
)
from app.ai.cloud import CloudProvider
from app.ai.factory import (
    available_providers,
    build_provider,
    register_provider,
)
from app.ai.fake import FakeProvider
from app.ai.ollama import OllamaProvider

__all__ = [
    "AIProvider",
    "ChatMessage",
    "CloudProvider",
    "FakeProvider",
    "FinishReason",
    "GenerationChunk",
    "GenerationResult",
    "MessageRole",
    "ModelInfo",
    "OllamaProvider",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "available_providers",
    "build_provider",
    "provider_error_code",
    "register_provider",
]

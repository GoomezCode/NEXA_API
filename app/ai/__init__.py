"""Contrato, registry e router dos provedores de IA (SRS §3.1.2/§3.2.6)."""

from app.ai.anthropic import AnthropicProvider
from app.ai.base import (
    AIProvider,
    Capabilities,
    ChatMessage,
    FinishReason,
    GatewayError,
    GenerationChunk,
    GenerationResult,
    MessageRole,
    ModelInfo,
    ModelNotAllowed,
    ModelNotFound,
    ProviderConfigurationError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
    ToolSpec,
    UnsupportedCapability,
    provider_error_code,
)
from app.ai.factory import (
    available_providers,
    build_provider,
    build_registry,
    provider_label,
    register_provider,
)
from app.ai.fake import FakeProvider
from app.ai.google import GoogleProvider
from app.ai.nvidia import NvidiaProvider
from app.ai.ollama import OllamaProvider
from app.ai.openai import OpenAIProvider
from app.ai.openai_compatible import OpenAICompatibleProvider
from app.ai.router import ProviderRouter, ResolvedModel, RoutedChunk, RoutedResult

__all__ = [
    "AIProvider",
    "AnthropicProvider",
    "Capabilities",
    "ChatMessage",
    "FakeProvider",
    "FinishReason",
    "GatewayError",
    "GenerationChunk",
    "GenerationResult",
    "GoogleProvider",
    "MessageRole",
    "ModelInfo",
    "ModelNotAllowed",
    "ModelNotFound",
    "NvidiaProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderRouter",
    "ProviderTimeout",
    "ProviderUnavailable",
    "ResolvedModel",
    "RoutedChunk",
    "RoutedResult",
    "ToolCall",
    "ToolSpec",
    "UnsupportedCapability",
    "available_providers",
    "build_provider",
    "build_registry",
    "provider_error_code",
    "provider_label",
    "register_provider",
]

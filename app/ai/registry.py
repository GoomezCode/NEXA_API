"""Fachada pública do registry de provedores (RF-GTW-01/10)."""

from __future__ import annotations

from app.ai.factory import (
    _REGISTRY,
    PROVIDER_LABELS,
    ProviderBuilder,
    available_providers,
    build_provider,
    build_registry,
    default_provider_name,
    enabled_provider_names,
    provider_label,
    register_provider,
)

__all__ = [
    "PROVIDER_LABELS",
    "_REGISTRY",
    "ProviderBuilder",
    "available_providers",
    "build_provider",
    "build_registry",
    "default_provider_name",
    "enabled_provider_names",
    "provider_label",
    "register_provider",
]

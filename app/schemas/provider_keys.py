"""Contrato de gerenciamento das chaves BYOK."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderName = Literal[
    "openai", "anthropic", "google", "ollama_cloud", "nvidia", "openai_compatible"
]


class ProviderKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1, max_length=8192, repr=False)
    base_url: str | None = Field(default=None, max_length=2048)
    default_model: str | None = Field(default=None, min_length=1, max_length=256)


class ProviderKeySummary(BaseModel):
    provider: ProviderName
    key_last4: str
    base_url: str | None
    default_model: str | None
    created_at: str
    updated_at: str
    last_used_at: str | None


class ProviderKeyTestResult(BaseModel):
    provider: ProviderName
    valid: bool
    message: str


__all__ = ["ProviderKeyInput", "ProviderKeySummary", "ProviderKeyTestResult", "ProviderName"]

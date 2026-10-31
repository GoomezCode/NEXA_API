"""Adapter para provedores que falam o contrato OpenAI (F12-06).

Cobre Groq, OpenRouter, DeepSeek, vLLM, LM Studio, Azure OpenAI e similares:
o protocolo é o mesmo da OpenAI, mudando apenas base URL, chave e modelo.
"""

from __future__ import annotations

from app.ai.openai import OpenAIProvider
from app.config import Settings


class OpenAICompatibleProvider(OpenAIProvider):
    """`OpenAIProvider` parametrizado por `OPENAI_COMPAT_*`."""

    def __init__(
        self,
        settings: Settings,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        logical_name = settings.openai_compat_name or "openai_compatible"
        super().__init__(
            settings,
            name="openai_compatible",
            base_url=base_url or settings.openai_compat_base_url,
            api_key=api_key if api_key is not None else settings.openai_compat_api_key_value,
            default_model=default_model or settings.openai_compat_default_model,
            label=f"provedor compatível ({logical_name})",
        )
        self.logical_name = logical_name


__all__ = ["OpenAICompatibleProvider"]

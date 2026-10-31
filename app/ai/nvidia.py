"""Adapter NVIDIA NIM para os endpoints de chat do API Catalog.

O endpoint genérico /v1/models também pode expor NIMs de outras tarefas.
Só publicamos aqui os IDs documentados pela NVIDIA para LLM chat completions.
"""

from __future__ import annotations

from app.ai.base import ModelInfo
from app.ai.openai import OpenAIProvider
from app.config import Settings

NVIDIA_API_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Sincronizado com a referência oficial NVIDIA LLM APIs. Modelos de extração,
# classificação, embedding e outros endpoints especializados ficam fora do chat.
NVIDIA_CHAT_MODELS = frozenset(
    {
        "deepseek-ai/deepseek-v4-flash",
        "deepseek-ai/deepseek-v4-flash-0731",
        "deepseek-ai/deepseek-v4-pro",
        "google/codegemma-7b",
        "google/gemma-7b",
        "meta/llama2-70b",
        "meta/llama-3.1-8b-instruct",
        "meta/llama-3.1-70b-instruct",
        "meta/llama-3.2-1b-instruct",
        "meta/llama-3.2-3b-instruct",
        "meta/llama-3.3-70b-instruct",
        "microsoft/phi-4-mini-instruct",
        "microsoft/phi-4-mini-flash-reasoning",
        "minimaxai/minimax-m2.5",
        "minimaxai/minimax-m2.7",
        "mistralai/mistral-nemotron",
        "mistralai/mixtral-8x7b-instruct",
        "mistralai/mixtral-8x22b-instruct",
        "moonshotai/kimi-k2-instruct",
        "moonshotai/kimi-k2-thinking",
        "moonshotai/kimi-k3",
        "nvidia/llama-3.1-nemoguard-8b-content-safety",
        "nvidia/llama-3.1-nemoguard-8b-topic-control",
        "nvidia/llama-3.1-nemotron-safety-guard-8b-v3",
        "nvidia/llama-3.3-nemotron-super-49b-v1",
        "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3.5-bolt-e4b",
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-content-safety-reasoning-4b",
        "nvidia/nvidia-nemotron-nano-9b-v2",
        "nvidia/riva-translate-4b-instruct-v2",
        "nvidia/usdcode",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "poolside/laguna-xs-2-1",
        "qwen/qwen2.5-coder-32b-instruct",
        "qwen/qwen3-next-80b-a3b-instruct",
        "qwen/qwen3-next-80b-a3b-thinking",
        "qwen/qwq-32b",
        "sarvamai/sarvam-m",
        "stepfun-ai/step-3.5-flash",
        "stockmark/stockmark-2-100b-instruct",
        "thinking-machines/inkling",
        "upstage/solar-10.7b-instruct",
        "z-ai/glm4.7",
        "z-ai/glm5.1",
        "z-ai/glm-5.2",
        "z-ai/glm-5.3",
        "z-ai/glm-5.3-flash",
    }
)


class NvidiaProvider(OpenAIProvider):
    """OpenAI-compatible adapter que limita o catálogo hospedado aos LLMs de chat."""

    def __init__(
        self,
        settings: Settings,
        *,
        name: str = "nvidia",
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        label: str = "NVIDIA NIM",
    ) -> None:
        super().__init__(
            settings,
            name=name,
            base_url=base_url or NVIDIA_API_BASE_URL,
            api_key=api_key,
            default_model=default_model,
            label=label,
        )
        self._filter_chat_catalog = (base_url or NVIDIA_API_BASE_URL).rstrip(
            "/"
        ) == NVIDIA_API_BASE_URL

    async def list_models(self, *, request_id: str | None = None) -> list[ModelInfo]:
        models = await super().list_models(request_id=request_id)
        if not self._filter_chat_catalog:
            return list(models)
        return [model for model in models if model.name in NVIDIA_CHAT_MODELS]


__all__ = ["NVIDIA_API_BASE_URL", "NVIDIA_CHAT_MODELS", "NvidiaProvider"]

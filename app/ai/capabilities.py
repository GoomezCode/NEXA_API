"""Validação de capacidades do gateway (RF-GTW-06, RN-24)."""

from __future__ import annotations

from collections.abc import Sequence

from app.ai.base import Capabilities, ModelInfo, ToolSpec, UnsupportedCapability

_CAPABILITY_LABELS = {
    "chat": "chat",
    "stream": "streaming",
    "tools": "ferramentas",
    "vision": "visão",
    "json_mode": "saída JSON",
}


def required_capabilities(
    *,
    stream: bool,
    tools: Sequence[ToolSpec] | None = None,
    has_images: bool = False,
    json_mode: bool = False,
) -> tuple[str, ...]:
    """Capacidades exigidas por uma requisição, em ordem estável."""
    required: list[str] = ["chat"]
    if stream:
        required.append("stream")
    if tools:
        required.append("tools")
    if has_images:
        required.append("vision")
    if json_mode:
        required.append("json_mode")
    return tuple(required)


def ensure_supported(
    capabilities: Capabilities,
    *,
    stream: bool,
    tools: Sequence[ToolSpec] | None = None,
    has_images: bool = False,
    json_mode: bool = False,
    model_id: str = "",
) -> None:
    """Recusa a requisição antes de chamar o provedor (sem consumir cota)."""
    for capability in required_capabilities(
        stream=stream, tools=tools, has_images=has_images, json_mode=json_mode
    ):
        if capabilities.supports(capability):
            continue
        label = _CAPABILITY_LABELS.get(capability, capability)
        target = f" para o modelo {model_id}" if model_id else ""
        raise UnsupportedCapability(
            f"Capacidade não suportada{target}: {label}.",
            capability=capability,
        )


def merge_capabilities(
    adapter: Capabilities,
    model: ModelInfo | None,
) -> Capabilities:
    """Interseção entre as capacidades do adapter e as do modelo.

    Modelos sem metadados herdam as capacidades do adapter (o provedor valida na
    chamada); quando o catálogo informa, prevalece o mais restritivo.
    """
    if model is None or model.capabilities is None:
        return adapter
    model_caps = model.capabilities
    return Capabilities(
        chat=adapter.chat and model_caps.chat,
        stream=adapter.stream and model_caps.stream,
        tools=adapter.tools and model_caps.tools,
        vision=adapter.vision and model_caps.vision,
        json_mode=adapter.json_mode and model_caps.json_mode,
        context_window=model.context_window or model_caps.context_window or adapter.context_window,
    )


def capability_names(capabilities: Capabilities) -> list[str]:
    """Lista estável das capacidades habilitadas, para o catálogo."""
    return [
        name
        for name in ("chat", "stream", "tools", "vision", "json_mode")
        if capabilities.supports(name)
    ]


__all__ = [
    "capability_names",
    "ensure_supported",
    "merge_capabilities",
    "required_capabilities",
]

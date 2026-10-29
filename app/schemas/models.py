"""Schemas do catálogo agregado de modelos (SRS v2.0, §3.7.6)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class ModelEntry(BaseModel):
    """Modelo disponível em um provedor."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(description="Model ID canônico `provider/model`.")
    name: str = Field(description="Nome completo no provedor (ex.: `llama3.1:latest`).")
    size_bytes: int | None = Field(default=None, description="Tamanho em bytes.")
    modified_at: datetime | None = Field(default=None, description="Última modificação, em UTC.")
    family: str | None = Field(default=None, description="Família do modelo.")
    parameter_size: str | None = Field(
        default=None, description="Quantidade de parâmetros (ex.: `8.0B`)."
    )
    quantization_level: str | None = Field(default=None, description="Quantização (ex.: `Q4_K_M`).")
    capabilities: list[str] = Field(
        description="Capacidades habilitadas: `chat`, `stream`, `tools`, `vision`, `json_mode`."
    )
    context_window: int | None = Field(default=None, description="Janela de contexto, em tokens.")
    selectable: bool = Field(
        description="Se pode ser escolhido em `POST /chat` (respeita `AI_ALLOWED_MODELS`)."
    )

    @field_serializer("modified_at")
    def _serialize_modified_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProviderModels(BaseModel):
    """Catálogo de um provedor habilitado."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(description="Nome do provedor no registry.")
    name: str = Field(description="Rótulo humano do provedor.")
    available: bool = Field(description="Se o catálogo respondeu nesta consulta.")
    default_model: str = Field(description="Modelo padrão do provedor.")
    models: list[ModelEntry] = Field(description="Modelos encontrados, ordenados por nome.")


class ModelIndexEntry(BaseModel):
    """Índice plano para clientes simples."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    provider: str
    name: str
    selectable: bool


class ModelsResponse(BaseModel):
    """Corpo de `GET /api/v1/models`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "default_model": "ollama/llama3.1",
                "providers": [
                    {
                        "provider": "ollama",
                        "name": "Ollama local",
                        "available": True,
                        "default_model": "llama3.1:latest",
                        "models": [
                            {
                                "model_id": "ollama/llama3.1",
                                "name": "llama3.1:latest",
                                "size_bytes": 4920739055,
                                "modified_at": "2026-10-28T14:30:00Z",
                                "family": "llama",
                                "parameter_size": "8.0B",
                                "quantization_level": "Q4_K_M",
                                "capabilities": ["chat", "stream", "tools"],
                                "context_window": 131072,
                                "selectable": True,
                            }
                        ],
                    }
                ],
                "models": [
                    {
                        "model_id": "ollama/llama3.1",
                        "provider": "ollama",
                        "name": "llama3.1:latest",
                        "selectable": True,
                    }
                ],
            }
        },
    )

    default_model: str = Field(description="Model ID usado quando `model` é omitido no chat.")
    providers: list[ProviderModels] = Field(description="Catálogo completo por provedor.")
    models: list[ModelIndexEntry] = Field(description="Índice plano de todos os modelos.")


__all__ = [
    "ModelEntry",
    "ModelIndexEntry",
    "ModelsResponse",
    "ProviderModels",
]

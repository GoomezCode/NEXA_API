"""Schemas do catálogo de modelos (SRS v1.2, §3.7.6)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class ModelEntry(BaseModel):
    """Modelo instalado no provedor de IA."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Nome completo, incluindo a tag (ex.: `llama3.1:latest`).")
    size_bytes: int | None = Field(default=None, description="Tamanho em bytes.")
    modified_at: datetime | None = Field(default=None, description="Última modificação, em UTC.")
    family: str | None = Field(default=None, description="Família do modelo.")
    parameter_size: str | None = Field(
        default=None, description="Quantidade de parâmetros (ex.: `8.0B`)."
    )
    quantization_level: str | None = Field(default=None, description="Quantização (ex.: `Q4_K_M`).")
    selectable: bool = Field(
        description="Se pode ser escolhido em `POST /chat` (respeita `AI_ALLOWED_MODELS`)."
    )

    @field_serializer("modified_at")
    def _serialize_modified_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ModelsResponse(BaseModel):
    """Corpo de `GET /api/v1/models`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "provider": "ollama",
                "default_model": "llama3.1:latest",
                "models": [
                    {
                        "name": "llama3.1:latest",
                        "size_bytes": 4920739055,
                        "modified_at": "2026-10-28T14:30:00Z",
                        "family": "llama",
                        "parameter_size": "8.0B",
                        "quantization_level": "Q4_K_M",
                        "selectable": True,
                    }
                ],
            }
        },
    )

    provider: str = Field(description="Provedor ativo.")
    default_model: str = Field(description="Modelo usado quando `model` é omitido no chat.")
    models: list[ModelEntry] = Field(description="Modelos instalados, ordenados por nome.")


__all__ = ["ModelEntry", "ModelsResponse"]

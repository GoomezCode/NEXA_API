"""Schemas dos endpoints de sistema (SRS §3.7.5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Corpo de `GET /health`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "status": "ok",
                "version": "2.0.0",
                "request_id": "b1f4c7e2-6f0f-4c39-9e57-6a1f0f0b8f4d",
            }
        },
    )

    status: Literal["ok"] = "ok"
    version: str = Field(description="Versão da API.")
    request_id: str = Field(description="Identificador da requisição (RF-SIS-03).")


class ProviderReadiness(BaseModel):
    """Disponibilidade de um provedor habilitado."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(description="Nome do provedor no registry.")
    available: bool = Field(description="Resultado do health check.")
    error: str | None = Field(default=None, description="Código do erro, quando indisponível.")


class ReadinessResponse(BaseModel):
    """Corpo de `GET /health/ready` (RF-SIS-04/05)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "status": "ok",
                "provider": "ollama",
                "providers": [{"provider": "ollama", "available": True, "error": None}],
                "database": "ok",
                "request_id": "b1f4c7e2-6f0f-4c39-9e57-6a1f0f0b8f4d",
            }
        },
    )

    status: Literal["ok", "unavailable"]
    provider: str | None = Field(default=None, description="Provedor padrão da API.")
    providers: list[ProviderReadiness] = Field(
        default_factory=list,
        description="Status de cada provedor habilitado.",
    )
    database: str | None = Field(default=None, description="Status do SQLite, quando configurado.")
    request_id: str = Field(description="Identificador da requisição (RF-SIS-03).")


__all__ = ["HealthResponse", "ProviderReadiness", "ReadinessResponse"]

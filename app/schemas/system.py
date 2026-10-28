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
                "version": "1.1.0",
                "request_id": "b1f4c7e2-6f0f-4c39-9e57-6a1f0f0b8f4d",
            }
        },
    )

    status: Literal["ok"] = "ok"
    version: str = Field(description="Versão da API.")
    request_id: str = Field(description="Identificador da requisição (RF-SIS-03).")


class ReadinessResponse(BaseModel):
    """Corpo de `GET /health/ready`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "status": "ok",
                "provider": "ollama",
                "request_id": "b1f4c7e2-6f0f-4c39-9e57-6a1f0f0b8f4d",
            }
        },
    )

    status: Literal["ok", "unavailable"]
    provider: str | None = Field(default=None, description="Provedor de IA configurado.")
    request_id: str = Field(description="Identificador da requisição (RF-SIS-03).")


__all__ = ["HealthResponse", "ReadinessResponse"]

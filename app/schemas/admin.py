"""Schemas da administração (SRS §3.7.4, RF-ADM-01 a 05)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

ResetScope = Literal["gid", "ip", "uid"]


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class StatsResponse(BaseModel):
    """Corpo de `GET /admin/stats`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "since": "2026-10-28T10:00:00Z",
                "chat_requests_total": 128,
                "guest_distinct": 14,
                "rate_limited_total": 7,
                "provider_errors_total": 2,
                "avg_response_seconds": 3.42,
            }
        },
    )

    since: datetime = Field(description="Início da contagem (boot da API), em UTC.")
    chat_requests_total: int = Field(description="Requisições de chat recebidas.")
    guest_distinct: int = Field(description="Guests distintos desde o boot.")
    rate_limited_total: int = Field(description="Mensagens barradas por limite.")
    provider_errors_total: int = Field(description="Falhas do provedor de IA.")
    avg_response_seconds: float = Field(description="Duração média das respostas, em segundos.")

    @field_serializer("since")
    def _serialize_since(self, value: datetime) -> str:
        return _iso_utc(value)


class ProviderStatus(BaseModel):
    """Saúde de um provedor habilitado (RF-ADM-03)."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(description="Nome do provedor no registry.")
    label: str = Field(description="Rótulo humano do provedor.")
    default_model: str = Field(description="Modelo padrão do provedor.")
    available: bool = Field(description="Resultado da última verificação de saúde.")
    checked_at: datetime = Field(description="Instante da verificação, em UTC.")
    error: str | None = Field(default=None, description="Código do erro, quando indisponível.")

    @field_serializer("checked_at")
    def _serialize_checked_at(self, value: datetime) -> str:
        return _iso_utc(value)


class AiStatusResponse(BaseModel):
    """Corpo de `GET /admin/ai/status` (nunca expõe chaves)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "providers": [
                    {
                        "provider": "ollama",
                        "label": "Ollama local",
                        "default_model": "llama3.1:latest",
                        "available": True,
                        "checked_at": "2026-10-29T14:30:00Z",
                        "error": None,
                    }
                ]
            }
        },
    )

    providers: list[ProviderStatus] = Field(description="Status de cada provedor habilitado.")


class GuestEntry(BaseModel):
    """Guest ativo em memória (RF-ADM-05)."""

    model_config = ConfigDict(extra="forbid")

    gid: str = Field(description="Identificador do guest (usado no reset).")
    ip: str = Field(description="IP da última requisição.")
    daily_used: int = Field(description="Mensagens consumidas hoje.")
    daily_limit: int = Field(description="Cota diária do guest.")
    last_seen_at: datetime = Field(description="Última atividade, em UTC.")

    @field_serializer("last_seen_at")
    def _serialize_last_seen_at(self, value: datetime) -> str:
        return _iso_utc(value)


class IpUsageEntry(BaseModel):
    """Uso diário agregado por IP."""

    model_config = ConfigDict(extra="forbid")

    ip: str = Field(description="IP do guest.")
    daily_used: int = Field(description="Mensagens consumidas hoje por este IP.")
    daily_limit: int = Field(description="Cota diária por IP.")


class GuestsResponse(BaseModel):
    """Corpo de `GET /admin/guests`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "guests": [
                    {
                        "gid": "6f1c9a3e-1b2d-4c5f-8a90-0123456789ab",
                        "ip": "203.0.113.10",
                        "daily_used": 4,
                        "daily_limit": 10,
                        "last_seen_at": "2026-10-28T14:25:00Z",
                    }
                ],
                "ip_usage": [{"ip": "203.0.113.10", "daily_used": 12, "daily_limit": 30}],
            }
        },
    )

    guests: list[GuestEntry]
    ip_usage: list[IpUsageEntry]


class ResetRequest(BaseModel):
    """Corpo de `POST /admin/usage/reset` (RF-ADM-04, RF-USO-10)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "scope": "gid",
                "value": "6f1c9a3e-1b2d-4c5f-8a90-0123456789ab",
            }
        },
    )

    scope: ResetScope = Field(description="Alvo do reset: `gid`, `ip` ou `uid`.")
    value: str = Field(
        min_length=1,
        max_length=256,
        description="`gid`, IP ou `uid` cuja cota diária será zerada.",
    )


__all__ = [
    "AiStatusResponse",
    "GuestEntry",
    "GuestsResponse",
    "IpUsageEntry",
    "ProviderStatus",
    "ResetRequest",
    "ResetScope",
    "StatsResponse",
]

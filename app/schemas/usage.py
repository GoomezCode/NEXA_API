"""Schema único do objeto `usage` (RD-08)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.core.security import Role


class UsageInfo(BaseModel):
    """Limites e uso restante do chamador (SRS §3.7.2)."""

    model_config = ConfigDict(extra="forbid")

    daily_limit: int | None = Field(description="Cota diária; `null` para o admin.")
    daily_remaining: int | None = Field(description="Uso diário restante; `null` para o admin.")
    per_minute_limit: int = Field(description="Limite por minuto.")
    per_minute_remaining: int = Field(description="Requisições restantes no minuto.")
    resets_at: datetime | None = Field(
        default=None, description="Próximo reset da cota diária (ISO 8601 UTC)."
    )

    @field_serializer("resets_at")
    def _serialize_resets_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class UsageResponse(UsageInfo):
    """Resposta de `GET /usage`, com o papel do chamador."""

    role: Role


__all__ = ["UsageInfo", "UsageResponse"]

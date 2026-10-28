"""Schemas de autenticação (SRS §3.7.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.security import Role
from app.schemas.usage import UsageInfo


class LoginRequest(BaseModel):
    """Credenciais do administrador."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"username": "admin", "password": "senha-do-administrador"}},
    )

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024, repr=False)


class TokenResponse(BaseModel):
    """Resposta do login do administrador."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 3600,
                "role": "admin",
            }
        },
    )

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    role: Role


class GuestTokenResponse(TokenResponse):
    """Resposta da emissão de token de convidado."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 86400,
                "role": "guest",
                "usage": {
                    "daily_limit": 10,
                    "daily_remaining": 10,
                    "per_minute_limit": 3,
                    "per_minute_remaining": 3,
                    "resets_at": "2026-10-29T00:00:00Z",
                },
            }
        },
    )

    role: Literal["guest"] = "guest"
    usage: UsageInfo


__all__ = ["GuestTokenResponse", "LoginRequest", "TokenResponse"]

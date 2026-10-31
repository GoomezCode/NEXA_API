"""Schemas de autenticação (SRS §3.7.1)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class RegisterRequest(BaseModel):
    """Cadastro público opcional."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "username": "marina",
                "email": "marina@example.com",
                "password": "senha-segura",
            }
        },
    )

    username: str = Field(min_length=3, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    password: str = Field(min_length=8, max_length=128, repr=False)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[a-zA-Z0-9._-]{3,64}", value):
            raise ValueError("use 3-64 caracteres: letras, números, ponto, hífen ou sublinhado")
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("e-mail inválido")
        return value


class TokenResponse(BaseModel):
    """Resposta do login (admin ou usuário de teste)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 3600,
                "role": "admin",
                "user_id": "00000000-0000-0000-0000-000000000001",
            }
        },
    )

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    role: Role
    user_id: str | None = Field(
        default=None,
        description="UUID do admin ou do usuário de teste; `null` para guest.",
    )


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


__all__ = ["GuestTokenResponse", "LoginRequest", "RegisterRequest", "TokenResponse"]

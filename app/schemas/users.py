"""Schemas de usuários e status do SQLite (SRS §3.7.4, RF-ADM-06 a 08)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.security import Role

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$")


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _UserBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=3,
        max_length=64,
        description="Username único, case-insensitive; é normalizado para minúsculas.",
    )
    email: str | None = Field(default=None, max_length=320, description="E-mail único opcional.")
    daily_message_limit: int | None = Field(
        default=None, ge=0, description="Cota diária; `null` usa o padrão."
    )
    rate_per_min: int | None = Field(
        default=None, ge=1, description="Limite por minuto; `null` usa o padrão."
    )
    expires_at: datetime | None = Field(
        default=None, description="Expiração do usuário de teste (ISO 8601 UTC)."
    )

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        value = value.strip()
        if not _USERNAME_PATTERN.match(value):
            raise ValueError("use 3-64 caracteres: letras, números, ponto, hífen ou sublinhado")
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not value:
            return None
        if not _EMAIL_PATTERN.match(value):
            raise ValueError("e-mail inválido")
        return value


class UserCreate(_UserBase):
    """Corpo de `POST /admin/users` (RF-USR-05)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "username": "teste01",
                "email": "teste01@example.com",
                "daily_message_limit": 100,
                "rate_per_min": 10,
                "expires_at": "2026-11-05T14:30:00Z",
            }
        },
    )

    password: str | None = Field(
        default=None,
        min_length=8,
        max_length=256,
        repr=False,
        description="Senha opcional; omitida, uma senha forte é gerada.",
    )


class UserUpdate(BaseModel):
    """Corpo de `PATCH /admin/users/{id}` (campos omitidos não mudam)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"daily_message_limit": 50, "is_active": False}},
    )

    username: str | None = Field(default=None, min_length=3, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    daily_message_limit: int | None = Field(default=None, ge=0)
    rate_per_min: int | None = Field(default=None, ge=1)
    is_active: bool | None = None
    expires_at: datetime | None = None

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not _USERNAME_PATTERN.match(value):
            raise ValueError("use 3-64 caracteres: letras, números, ponto, hífen ou sublinhado")
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not value:
            return None
        if not _EMAIL_PATTERN.match(value):
            raise ValueError("e-mail inválido")
        return value


class UserResponse(BaseModel):
    """Usuário exposto pela API (nunca inclui `password_hash`)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "id": "0192f3a4-0000-7000-8000-000000000001",
                "username": "teste01",
                "username_display": "teste01",
                "email": "teste01@example.com",
                "role": "user",
                "is_test": True,
                "is_protected": False,
                "is_active": True,
                "daily_message_limit": 100,
                "rate_per_min": 10,
                "created_at": "2026-10-29T14:30:00Z",
                "updated_at": "2026-10-29T14:30:00Z",
                "last_login_at": None,
                "expires_at": "2026-11-05T14:30:00Z",
            }
        },
    )

    id: str
    username: str
    username_display: str
    email: str | None = None
    role: Role
    is_test: bool
    is_protected: bool
    is_active: bool
    daily_message_limit: int | None = None
    rate_per_min: int | None = None
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None
    expires_at: datetime | None = None

    @field_serializer("created_at", "updated_at")
    def _serialize_required_time(self, value: datetime) -> str:
        return iso_utc(value) or ""

    @field_serializer("last_login_at", "expires_at")
    def _serialize_optional_time(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class UserCreateResponse(UserResponse):
    """Resposta de criação/redefinição; `password` aparece uma única vez."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "id": "0192f3a4-0000-7000-8000-000000000001",
                "username": "teste01",
                "username_display": "teste01",
                "email": None,
                "role": "user",
                "is_test": True,
                "is_protected": False,
                "is_active": True,
                "daily_message_limit": 100,
                "rate_per_min": 10,
                "created_at": "2026-10-29T14:30:00Z",
                "updated_at": "2026-10-29T14:30:00Z",
                "last_login_at": None,
                "expires_at": "2026-11-05T14:30:00Z",
                "password": "Nxa-exemplo-de-senha-gerada",
            }
        },
    )

    password: str = Field(description="Senha em texto puro; devolvida apenas nesta resposta.")


class UserPage(BaseModel):
    """Resposta de `GET /admin/users`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "users": [],
                "next_cursor": None,
            }
        },
    )

    users: list[UserResponse]
    next_cursor: str | None = Field(default=None, description="Cursor opaco para a próxima página.")


class BulkCreateRequest(BaseModel):
    """Corpo de `POST /admin/users/bulk` (RF-USR-06, RF-ADM-10)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "prefix": "teste",
                "count": 20,
                "daily_message_limit": 50,
                "rate_per_min": 5,
                "expires_at": "2026-11-05T14:30:00Z",
            }
        },
    )

    prefix: str = Field(
        min_length=1,
        max_length=56,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,55}$",
        description="Prefixo dos usernames sequenciais.",
    )
    count: int = Field(ge=1, le=1000, description="Quantidade de usuários (respeita o teto).")
    daily_message_limit: int | None = Field(default=None, ge=0)
    rate_per_min: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = Field(default=None)


class BulkCreateResponse(BaseModel):
    """Resposta de `POST /admin/users/bulk` (credenciais uma única vez)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "users": [
                    {
                        "id": "0192f3a4-0000-7000-8000-000000000001",
                        "username": "teste01",
                        "username_display": "teste01",
                        "email": None,
                        "role": "user",
                        "is_test": True,
                        "is_protected": False,
                        "is_active": True,
                        "daily_message_limit": 50,
                        "rate_per_min": 5,
                        "created_at": "2026-10-29T14:30:00Z",
                        "updated_at": "2026-10-29T14:30:00Z",
                        "last_login_at": None,
                        "expires_at": "2026-11-05T14:30:00Z",
                        "password": "Nxa-exemplo-de-senha-gerada",
                    }
                ]
            }
        },
    )

    users: list[UserCreateResponse]


class ResetPasswordRequest(BaseModel):
    """Corpo opcional de `POST /admin/users/{id}/reset-password`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"password": "Nxa-nova-senha-opcional"}},
    )

    password: str | None = Field(
        default=None,
        min_length=8,
        max_length=256,
        repr=False,
        description="Nova senha; omitida, uma senha forte é gerada.",
    )


class ResetPasswordResponse(BaseModel):
    """Resposta da redefinição de senha."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "id": "0192f3a4-0000-7000-8000-000000000001",
                "username": "teste01",
                "password": "Nxa-exemplo-de-senha-gerada",
            }
        },
    )

    id: str
    username: str
    password: str = Field(description="Nova senha em texto puro (única exibição).")


class CleanupResponse(BaseModel):
    """Resposta de `POST /admin/users/cleanup`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"removed": 3}},
    )

    removed: int = Field(ge=0, description="Usuários de teste expirados removidos.")


class DbStatusResponse(BaseModel):
    """Resposta de `GET /admin/db/status` (RF-ADM-07)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "status": "ok",
                "path": "./data/nexa.db",
                "size_bytes": 286720,
                "wal": True,
                "migration_version": 2,
                "users_total": 42,
                "test_users_total": 41,
                "read_latency_ms_p95": 0.8,
                "write_latency_ms_p95": 2.1,
            }
        },
    )

    status: str = Field(description="Estado do banco (`ok`).")
    path: str = Field(description="Caminho do arquivo SQLite.")
    size_bytes: int = Field(description="Tamanho do arquivo principal.")
    wal: bool = Field(description="Se o journaling WAL está ativo.")
    migration_version: int = Field(description="Maior versão de migração aplicada.")
    users_total: int = Field(description="Total de usuários.")
    test_users_total: int = Field(description="Total de usuários de teste.")
    read_latency_ms_p95: float = Field(description="p95 das leituras desde o boot (ms).")
    write_latency_ms_p95: float = Field(description="p95 das escritas desde o boot (ms).")


__all__ = [
    "BulkCreateRequest",
    "BulkCreateResponse",
    "CleanupResponse",
    "DbStatusResponse",
    "ResetPasswordRequest",
    "ResetPasswordResponse",
    "UserCreate",
    "UserCreateResponse",
    "UserPage",
    "UserResponse",
    "UserUpdate",
    "iso_utc",
]

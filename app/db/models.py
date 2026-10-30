"""Modelos internos do SQLite de usuários (RF-USR-01, RF-USR-13)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

#: ID fixo do administrador (invariante 2 de docs/database-sqlite.md).
ADMIN_ID = "00000000-0000-0000-0000-000000000001"

UserRole = Literal["admin", "user", "guest"]


def to_db_time(value: datetime | None) -> str | None:
    """Serializa um instante em UTC com microssegundos (ordem lexicográfica)."""
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_db_time(value: str | None) -> datetime | None:
    """Interpreta um instante gravado por `to_db_time`."""
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class UserRow:
    """Linha completa da tabela `users`."""

    id: str
    username: str
    username_display: str
    email: str | None
    password_hash: str
    role: UserRole
    is_test: bool
    is_protected: bool
    is_active: bool
    daily_message_limit: int | None
    rate_per_min: int | None
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
    expires_at: datetime | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def expired_at(self, now: datetime) -> bool:
        """`True` quando o usuário de teste já passou de `expires_at`."""
        return self.expires_at is not None and self.expires_at <= now

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | sqlite3.Row) -> UserRow:
        """Converte uma linha do SQLite em `UserRow`."""
        created_at = from_db_time(cast(str, row["created_at"]))
        updated_at = from_db_time(cast(str, row["updated_at"]))
        if created_at is None or updated_at is None:
            raise ValueError("created_at/updated_at ausente na linha de usuário")
        return cls(
            id=cast(str, row["id"]),
            username=cast(str, row["username"]),
            username_display=cast(str, row["username_display"]),
            email=cast(str | None, row["email"]),
            password_hash=cast(str, row["password_hash"]),
            role=cast(UserRole, row["role"]),
            is_test=bool(row["is_test"]),
            is_protected=bool(row["is_protected"]),
            is_active=bool(row["is_active"]),
            daily_message_limit=cast(int | None, row["daily_message_limit"]),
            rate_per_min=cast(int | None, row["rate_per_min"]),
            created_at=created_at,
            updated_at=updated_at,
            last_login_at=from_db_time(cast(str | None, row["last_login_at"])),
            expires_at=from_db_time(cast(str | None, row["expires_at"])),
        )


__all__ = [
    "ADMIN_ID",
    "UserRole",
    "UserRow",
    "from_db_time",
    "to_db_time",
]

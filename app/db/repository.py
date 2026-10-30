"""Repositório de usuários com SQL parametrizado e cursor (F13-03)."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.db.connection import Database
from app.db.models import UserRow, to_db_time

#: Colunas explícitas (proibido `SELECT *` — RF-USR-13).
USER_COLUMNS = (
    "id, username, username_display, email, password_hash, role, is_test, "
    "is_protected, is_active, daily_message_limit, rate_per_min, created_at, "
    "updated_at, last_login_at, expires_at"
)

_UPDATE_COLUMNS = frozenset(
    {
        "username",
        "username_display",
        "email",
        "password_hash",
        "daily_message_limit",
        "rate_per_min",
        "is_active",
        "expires_at",
        "updated_at",
    }
)

#: Consultas críticas expostas para o teste de `EXPLAIN QUERY PLAN` (F13-08).
USER_LOOKUP_SQL = f"SELECT {USER_COLUMNS} FROM users WHERE username = ? AND is_active = 1"
USER_BY_ID_SQL = f"SELECT {USER_COLUMNS} FROM users WHERE id = ?"

LIST_USERS_SQL = f"""
            SELECT {USER_COLUMNS}
            FROM users
            WHERE (? IS NULL OR (created_at, id) > (?, ?))
              AND (? IS NULL OR role = ?)
              AND (? IS NULL OR is_test = ?)
              AND (? IS NULL OR is_active = ?)
            ORDER BY created_at, id
            LIMIT ?
            """

CLEANUP_EXPIRED_SQL = """
                DELETE FROM users
                WHERE is_test = 1
                  AND is_protected = 0
                  AND expires_at IS NOT NULL
                  AND expires_at < ?
                """

MAX_PAGE_SIZE = 100


def encode_cursor(created_at: datetime, user_id: str) -> str:
    """Cursor opaco `(created_at, id)` em base64url."""
    raw = f"{to_db_time(created_at)}|{user_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[str, str]:
    """Decodifica o cursor; `ValueError` quando malformado."""
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("cursor inválido") from exc
    created_at, separator, user_id = raw.partition("|")
    if not separator or not created_at or not user_id:
        raise ValueError("cursor inválido")
    return created_at, user_id


class UserRepository:
    """Acesso à tabela `users`; escritas curtas e leituras pelo pool."""

    def __init__(self, database: Database) -> None:
        self._database = database

    # --- Leitura ------------------------------------------------------------
    async def get_by_username(self, username: str) -> UserRow | None:
        row = await self._database.fetch_one(USER_LOOKUP_SQL, (username,))
        return UserRow.from_row(row) if row is not None else None

    async def get_by_id(self, user_id: str) -> UserRow | None:
        row = await self._database.fetch_one(USER_BY_ID_SQL, (user_id,))
        return UserRow.from_row(row) if row is not None else None

    async def list_users(
        self,
        *,
        limit: int,
        cursor: tuple[str, str] | None = None,
        role: str | None = None,
        is_test: bool | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[UserRow], str | None]:
        """Lista por cursor `(created_at, id)`; máximo de 100 linhas."""
        capped = max(1, min(limit, MAX_PAGE_SIZE))
        cursor_created = cursor[0] if cursor is not None else None
        cursor_id = cursor[1] if cursor is not None else None
        rows = await self._database.fetch_all(
            LIST_USERS_SQL,
            (
                cursor_created,
                cursor_created,
                cursor_id,
                role,
                role,
                None if is_test is None else int(is_test),
                None if is_test is None else int(is_test),
                None if is_active is None else int(is_active),
                None if is_active is None else int(is_active),
                capped + 1,
            ),
        )
        items = [UserRow.from_row(row) for row in rows]
        if len(items) <= capped:
            return items, None
        page = items[:capped]
        last = page[-1]
        return page, encode_cursor(last.created_at, last.id)

    async def find_usernames_with_prefix(self, prefix: str) -> list[str]:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = await self._database.fetch_all(
            "SELECT username FROM users WHERE username LIKE ? ESCAPE '\\'",
            (f"{escaped}%",),
        )
        return [str(row["username"]) for row in rows]

    async def stats(self) -> tuple[int, int]:
        """`(total, usuários de teste)` para `/admin/db/status`."""
        row = await self._database.fetch_one(
            "SELECT COUNT(*) AS total, COALESCE(SUM(is_test), 0) AS test_total FROM users"
        )
        if row is None:
            return 0, 0
        return int(row["total"]), int(row["test_total"])

    # --- Escrita ------------------------------------------------------------
    async def create(self, user: UserRow) -> UserRow:
        async with self._database.write() as connection:
            await connection.execute(
                f"""
                INSERT INTO users ({USER_COLUMNS})
                VALUES ({", ".join("?" for _ in range(15))})
                """,
                _row_values(user),
            )
        return user

    async def create_many(self, users: Sequence[UserRow]) -> None:
        if not users:
            return
        async with self._database.write() as connection:
            await connection.executemany(
                f"""
                INSERT INTO users ({USER_COLUMNS})
                VALUES ({", ".join("?" for _ in range(15))})
                """,
                [_row_values(user) for user in users],
            )

    async def update(self, user_id: str, fields: Mapping[str, Any]) -> UserRow | None:
        invalid = set(fields) - _UPDATE_COLUMNS
        if invalid:
            raise ValueError(f"colunas não atualizáveis: {', '.join(sorted(invalid))}")
        if not fields:
            return await self.get_by_id(user_id)
        assignments = ", ".join(f"{column} = ?" for column in fields)
        params = [*fields.values(), user_id]
        async with self._database.write() as connection:
            cursor = await connection.execute(
                f"UPDATE users SET {assignments} WHERE id = ?",
                tuple(params),
            )
            changed = cursor.rowcount
            await cursor.close()
        if changed == 0:
            return None
        return await self.get_by_id(user_id)

    async def delete_test_user(self, user_id: str) -> bool:
        async with self._database.write() as connection:
            cursor = await connection.execute(
                "DELETE FROM users WHERE id = ? AND is_test = 1 AND is_protected = 0",
                (user_id,),
            )
            changed = cursor.rowcount
            await cursor.close()
        return changed > 0

    async def cleanup_expired(self, now: datetime) -> int:
        async with self._database.write() as connection:
            cursor = await connection.execute(
                CLEANUP_EXPIRED_SQL,
                (to_db_time(now),),
            )
            removed = cursor.rowcount
            await cursor.close()
        return removed

    async def update_last_login(self, user_id: str, now: datetime) -> None:
        async with self._database.write() as connection:
            await connection.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (to_db_time(now), user_id),
            )


def _row_values(user: UserRow) -> tuple[Any, ...]:
    return (
        user.id,
        user.username,
        user.username_display,
        user.email,
        user.password_hash,
        user.role,
        int(user.is_test),
        int(user.is_protected),
        int(user.is_active),
        user.daily_message_limit,
        user.rate_per_min,
        to_db_time(user.created_at),
        to_db_time(user.updated_at),
        to_db_time(user.last_login_at),
        to_db_time(user.expires_at),
    )


__all__ = [
    "CLEANUP_EXPIRED_SQL",
    "LIST_USERS_SQL",
    "MAX_PAGE_SIZE",
    "USER_BY_ID_SQL",
    "USER_COLUMNS",
    "USER_LOOKUP_SQL",
    "UserRepository",
    "decode_cursor",
    "encode_cursor",
]

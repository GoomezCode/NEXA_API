"""Regras de usuários de teste, cache de autenticação e guardas (F13-05)."""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import uuid4

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import ApiError, ValidationErrorItem
from app.core.logging import get_logger
from app.core.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.db.models import UserRow, to_db_time
from app.db.repository import UserRepository
from app.schemas.users import BulkCreateRequest, UserCreate, UserUpdate

T = TypeVar("T")

#: Intervalo da limpeza automática de usuários de teste expirados (RF-USR-08).
CLEANUP_INTERVAL_SECONDS = 900.0

#: Caracteres da senha gerada (além do prefixo `Nxa-`).
GENERATED_PASSWORD_BYTES = 18


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    row: UserRow
    expires_at: datetime


def generate_password() -> str:
    """Senha forte devolvida uma única vez ao administrador (RF-USR-06)."""
    return f"Nxa-{secrets.token_urlsafe(GENERATED_PASSWORD_BYTES)}"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class UserService:
    """Cria/atualiza usuários de teste, autentica e mantém o cache curto."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        repository: UserRepository | None = None,
        *,
        cleanup_interval_seconds: float = CLEANUP_INTERVAL_SECONDS,
    ) -> None:
        if cleanup_interval_seconds <= 0:
            raise ValueError("cleanup_interval_seconds deve ser > 0")
        self._settings = settings
        self._clock = clock
        self._repository = repository
        self._cleanup_interval = cleanup_interval_seconds
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._task: asyncio.Task[None] | None = None
        self._logger = get_logger("nexa.users")

    # --- Ciclo de vida ------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._repository is not None

    def attach(self, repository: UserRepository) -> None:
        """Liga o repositório depois que o banco/migrações estão prontos."""
        self._repository = repository
        self._cache.clear()

    async def start(self) -> None:
        if self._repository is None:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._cleanup_loop())

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    # --- Cache/autenticação -------------------------------------------------
    async def get_by_username(self, username: str) -> UserRow | None:
        key = f"u:{username.strip().lower()}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        repository = self._require_repository()
        row = await self._guard(repository.get_by_username(username.strip()))
        if row is not None:
            self._cache_put(row)
        # Sem cache negativo: um usuário recém-criado precisa autenticar já.
        return row

    async def get_by_id(self, user_id: str) -> UserRow | None:
        key = f"i:{user_id}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        repository = self._require_repository()
        row = await self._guard(repository.get_by_id(user_id))
        if row is not None:
            self._cache_put(row)
        return row

    async def list_users(
        self,
        *,
        limit: int,
        cursor: tuple[str, str] | None,
        role: str | None,
        is_test: bool | None,
        is_active: bool | None,
    ) -> tuple[list[UserRow], str | None]:
        repository = self._require_repository()
        return await self._guard(
            repository.list_users(
                limit=limit,
                cursor=cursor,
                role=role,
                is_test=is_test,
                is_active=is_active,
            )
        )

    async def authenticate(self, username: str, password: str) -> UserRow | None:
        """Valida um usuário de teste; `None` para credenciais inválidas."""
        row = await self.get_by_username(username)
        if row is None:
            # Custo equivalente ao de uma senha existente (RF-AUT-04).
            await asyncio.to_thread(verify_password, password, DUMMY_PASSWORD_HASH)
            return None
        valid = await asyncio.to_thread(verify_password, password, row.password_hash)
        if not valid or not row.is_active or row.expired_at(self._clock.now()):
            return None
        return row

    async def resolve_token(self, user_id: str) -> UserRow:
        """Valida o `sub` de um JWT `user` (desativação invalida tokens — RF-USR-14)."""
        row = await self.get_by_id(user_id)
        if (
            row is None
            or row.role != "user"
            or not row.is_active
            or row.expired_at(self._clock.now())
        ):
            raise ApiError("invalid_token")
        return row

    async def mark_login(self, user_id: str) -> None:
        try:
            repository = self._repository
            if repository is not None:
                await self._guard(repository.update_last_login(user_id, self._clock.now()))
        except ApiError:
            # `last_login_at` não pode impedir um login já validado.
            self._logger.warning("last_login_update_failed")

    # --- Criação ------------------------------------------------------------
    async def create_test_user(self, payload: UserCreate) -> tuple[UserRow, str]:
        self._require_test_users()
        password = payload.password or generate_password()
        password_hash = await asyncio.to_thread(hash_password, password)
        row = self._build_row(
            username=payload.username,
            password_hash=password_hash,
            email=payload.email,
            daily_message_limit=payload.daily_message_limit,
            rate_per_min=payload.rate_per_min,
            expires_at=payload.expires_at,
        )
        row = await self._create(row)
        self._logger.info("test_user_created", username=row.username)
        return row, password

    async def create_bulk(self, payload: BulkCreateRequest) -> list[tuple[UserRow, str]]:
        self._require_test_users()
        if payload.count > self._settings.test_user_max_bulk:
            raise ApiError(
                "validation_error",
                errors=[
                    ValidationErrorItem(
                        field="count",
                        reason=f"no máximo {self._settings.test_user_max_bulk} usuários por lote",
                    )
                ],
            )

        repository = self._require_repository()
        prefix = payload.prefix.strip().lower()
        existing = await self._guard(repository.find_usernames_with_prefix(prefix))
        start = max((_numeric_suffix(name, prefix) for name in existing), default=0) + 1

        passwords = [generate_password() for _ in range(payload.count)]
        hashes = await asyncio.gather(
            *(asyncio.to_thread(hash_password, password) for password in passwords)
        )
        now = self._clock.now()
        rows: list[UserRow] = []
        for offset, password_hash in enumerate(hashes):
            row = self._build_row(
                username=f"{prefix}{start + offset:02d}",
                password_hash=password_hash,
                email=None,
                daily_message_limit=payload.daily_message_limit,
                rate_per_min=payload.rate_per_min,
                expires_at=payload.expires_at,
                created_at=now + timedelta(microseconds=offset),
            )
            rows.append(row)

        try:
            await self._guard(repository.create_many(rows))
        except sqlite3.IntegrityError as exc:
            raise ApiError("username_conflict") from exc
        for row in rows:
            self._cache_put(row)
        self._logger.info("test_users_bulk_created", count=len(rows), prefix=prefix)
        return list(zip(rows, passwords, strict=True))

    # --- Atualização/exclusão ----------------------------------------------
    async def update_user(self, user_id: str, payload: UserUpdate) -> UserRow:
        row = await self._require_user(user_id)
        self._ensure_editable(row)
        now = self._clock.now()
        fields: dict[str, object] = {"updated_at": to_db_time(now)}
        if "username" in payload.model_fields_set and payload.username is not None:
            fields["username"] = payload.username.strip().lower()
            fields["username_display"] = payload.username.strip()
        if "email" in payload.model_fields_set:
            fields["email"] = payload.email
        if "daily_message_limit" in payload.model_fields_set:
            fields["daily_message_limit"] = payload.daily_message_limit
        if "rate_per_min" in payload.model_fields_set:
            fields["rate_per_min"] = payload.rate_per_min
        if "is_active" in payload.model_fields_set and payload.is_active is not None:
            fields["is_active"] = int(payload.is_active)
        if "expires_at" in payload.model_fields_set:
            fields["expires_at"] = to_db_time(_as_utc(payload.expires_at))

        repository = self._require_repository()
        try:
            updated = await self._guard(repository.update(user_id, fields))
        except sqlite3.IntegrityError as exc:
            raise ApiError("username_conflict") from exc
        if updated is None:
            raise ApiError("not_found")

        self._invalidate(username=row.username, user_id=row.id)
        self._cache_put(updated)
        self._logger.info("test_user_updated", username=updated.username)
        return updated

    async def reset_password(self, user_id: str, password: str | None) -> tuple[UserRow, str]:
        row = await self._require_user(user_id)
        self._ensure_editable(row)
        new_password = password or generate_password()
        password_hash = await asyncio.to_thread(hash_password, new_password)
        repository = self._require_repository()
        updated = await self._guard(
            repository.update(
                user_id,
                {
                    "password_hash": password_hash,
                    "updated_at": to_db_time(self._clock.now()),
                },
            )
        )
        if updated is None:
            raise ApiError("not_found")
        self._invalidate(username=row.username, user_id=row.id)
        self._cache_put(updated)
        self._logger.info("test_user_password_reset", username=updated.username)
        return updated, new_password

    async def delete_user(self, user_id: str) -> None:
        row = await self._require_user(user_id)
        self._ensure_editable(row)
        if not row.is_test:
            raise ApiError("user_not_deletable")
        repository = self._require_repository()
        deleted = await self._guard(repository.delete_test_user(user_id))
        if not deleted:
            raise ApiError("not_found")
        self._invalidate(username=row.username, user_id=row.id)
        self._logger.info("test_user_deleted", username=row.username)

    async def stats(self) -> tuple[int, int]:
        """`(total, usuários de teste)` para `/admin/db/status`."""
        repository = self._require_repository()
        return await self._guard(repository.stats())

    async def cleanup_expired(self) -> int:
        repository = self._repository
        if repository is None:
            return 0
        removed = await self._guard(repository.cleanup_expired(self._clock.now()))
        if removed:
            self._cache.clear()
            self._logger.info("expired_test_users_cleaned", removed=removed)
        return removed

    # --- Internos -----------------------------------------------------------
    def _build_row(
        self,
        *,
        username: str,
        password_hash: str,
        email: str | None,
        daily_message_limit: int | None,
        rate_per_min: int | None,
        expires_at: datetime | None,
        created_at: datetime | None = None,
    ) -> UserRow:
        now = created_at or self._clock.now()
        normalized = username.strip().lower()
        expiration = _as_utc(expires_at)
        if expiration is None and self._settings.test_user_ttl_hours > 0:
            expiration = now + timedelta(hours=self._settings.test_user_ttl_hours)
        return UserRow(
            id=str(uuid4()),
            username=normalized,
            username_display=username.strip(),
            email=email.strip().lower() if email else None,
            password_hash=password_hash,
            role="user",
            is_test=True,
            is_protected=False,
            is_active=True,
            daily_message_limit=(
                self._settings.test_user_default_daily_messages
                if daily_message_limit is None
                else daily_message_limit
            ),
            rate_per_min=(
                self._settings.test_user_default_rate_per_min
                if rate_per_min is None
                else rate_per_min
            ),
            created_at=now,
            updated_at=now,
            last_login_at=None,
            expires_at=expiration,
        )

    async def _create(self, row: UserRow) -> UserRow:
        repository = self._require_repository()
        try:
            created = await self._guard(repository.create(row))
        except sqlite3.IntegrityError as exc:
            raise ApiError("username_conflict") from exc
        self._cache_put(created)
        return created

    async def _require_user(self, user_id: str) -> UserRow:
        row = await self.get_by_id(user_id)
        if row is None:
            raise ApiError("not_found")
        return row

    @staticmethod
    def _ensure_editable(row: UserRow) -> None:
        if row.is_protected or row.is_admin:
            raise ApiError("admin_protected")

    def _require_test_users(self) -> None:
        if not self._settings.allow_test_users:
            raise ApiError("test_users_disabled")

    def _require_repository(self) -> UserRepository:
        if self._repository is None:
            raise ApiError("database_unavailable")
        return self._repository

    async def _guard(self, operation: Awaitable[T]) -> T:
        try:
            return await operation
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            self._logger.warning("database_operation_failed")
            raise ApiError("database_unavailable") from exc

    # --- Cache LRU ----------------------------------------------------------
    def _cache_get(self, key: str) -> UserRow | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock.now():
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return entry.row

    def _cache_put(self, row: UserRow) -> None:
        ttl = self._settings.users_cache_ttl_seconds
        if ttl <= 0:
            return
        expires_at = self._clock.now() + timedelta(seconds=ttl)
        for key in (f"u:{row.username}", f"i:{row.id}"):
            self._cache[key] = _CacheEntry(row=row, expires_at=expires_at)
            self._cache.move_to_end(key)
        while len(self._cache) > self._settings.users_cache_max_entries:
            self._cache.popitem(last=False)

    def _invalidate(self, *, username: str | None = None, user_id: str | None = None) -> None:
        if username is not None:
            self._cache.pop(f"u:{username.strip().lower()}", None)
        if user_id is not None:
            self._cache.pop(f"i:{user_id}", None)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self._cleanup_interval)
            try:
                await self.cleanup_expired()
            except ApiError:
                self._logger.warning("expired_test_users_cleanup_failed")


def _numeric_suffix(username: str, prefix: str) -> int:
    if not username.startswith(prefix):
        return 0
    tail = username[len(prefix) :]
    return int(tail) if tail.isdigit() else 0


__all__ = [
    "CLEANUP_INTERVAL_SECONDS",
    "GENERATED_PASSWORD_BYTES",
    "UserService",
    "generate_password",
]

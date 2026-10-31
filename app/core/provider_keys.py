"""Armazenamento cifrado de chaves de provedor por usuário."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import ApiError
from app.db.connection import Database
from app.db.models import to_db_time

PROVIDERS = frozenset({"openai", "anthropic", "google", "ollama_cloud", "openai_compatible"})


@dataclass(frozen=True, slots=True)
class StoredProviderKey:
    user_id: str
    provider: str
    api_key: str
    base_url: str | None
    default_model: str | None
    created_at: str
    updated_at: str
    last_used_at: str | None


@dataclass(frozen=True, slots=True)
class ProviderKeySummary:
    provider: str
    key_last4: str
    base_url: str | None
    default_model: str | None
    created_at: str
    updated_at: str
    last_used_at: str | None


class ProviderKeyStore:
    def __init__(self, database: Database, settings: Settings, clock: Clock) -> None:
        self._database = database
        self._clock = clock
        secret = settings.key_encryption_secret
        if secret is None:
            raise ApiError("provider_keys_unavailable")
        digest = hashlib.sha256(secret.get_secret_value().encode()).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self._cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._cipher.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise ApiError("provider_keys_unavailable") from exc

    async def save(
        self,
        user_id: str,
        provider: str,
        api_key: str,
        base_url: str | None,
        default_model: str | None,
    ) -> None:
        now = to_db_time(self._clock.now())
        encrypted = self.encrypt(api_key)
        async with self._database.write() as connection:
            await connection.execute(
                """INSERT INTO user_provider_keys
                (user_id, provider, api_key_encrypted, base_url, default_model,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                  api_key_encrypted=excluded.api_key_encrypted, base_url=excluded.base_url,
                  default_model=excluded.default_model, updated_at=excluded.updated_at""",
                (user_id, provider, encrypted, base_url, default_model, now, now),
            )

    async def get(self, user_id: str, provider: str) -> StoredProviderKey | None:
        row = await self._database.fetch_one(
            """SELECT user_id, provider, api_key_encrypted, base_url, default_model,
            created_at, updated_at, last_used_at FROM user_provider_keys
            WHERE user_id=? AND provider=?""",
            (user_id, provider),
        )
        if row is None:
            return None
        return StoredProviderKey(
            user_id=str(row["user_id"]),
            provider=str(row["provider"]),
            api_key=self.decrypt(str(row["api_key_encrypted"])),
            base_url=row["base_url"],
            default_model=row["default_model"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_used_at=row["last_used_at"],
        )

    async def list(self, user_id: str) -> list[ProviderKeySummary]:
        rows = await self._database.fetch_all(
            """SELECT provider, api_key_encrypted, base_url, default_model,
            created_at, updated_at, last_used_at FROM user_provider_keys
            WHERE user_id=? ORDER BY provider""",
            (user_id,),
        )
        return [
            ProviderKeySummary(
                provider=str(row["provider"]),
                key_last4=self.decrypt(str(row["api_key_encrypted"]))[-4:],
                base_url=row["base_url"],
                default_model=row["default_model"],
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                last_used_at=row["last_used_at"],
            )
            for row in rows
        ]

    async def mark_used(self, user_id: str, provider: str) -> None:
        async with self._database.write() as connection:
            await connection.execute(
                "UPDATE user_provider_keys SET last_used_at=? WHERE user_id=? AND provider=?",
                (to_db_time(self._clock.now()), user_id, provider),
            )

    async def delete(self, user_id: str, provider: str) -> bool:
        async with self._database.write() as connection:
            cursor = await connection.execute(
                "DELETE FROM user_provider_keys WHERE user_id=? AND provider=?", (user_id, provider)
            )
            return cursor.rowcount > 0


__all__ = ["PROVIDERS", "ProviderKeyStore", "ProviderKeySummary", "StoredProviderKey"]

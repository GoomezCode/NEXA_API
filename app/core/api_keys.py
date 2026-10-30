"""API keys NEXA das rotas de compatibilidade (RF-AUT-11/13, RF-CMP-05)."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.config import Settings

#: Escopos aceitos pelas chaves de compatibilidade.
SCOPE_CHAT = "chat"
SCOPE_MODELS = "models"
DEFAULT_SCOPES = frozenset({SCOPE_CHAT, SCOPE_MODELS})

#: Quantidade de caracteres do segredo exposta em logs (`nxa_live`).
PREFIX_LENGTH = 8

#: Bytes do SHA-256 usados no fingerprint (12 caracteres hexadecimais).
FINGERPRINT_BYTES = 6


@dataclass(frozen=True, slots=True)
class ApiKeyIdentity:
    """Identidade derivada de uma API key; nunca guarda o segredo."""

    prefix: str
    fingerprint: str
    scopes: frozenset[str] = field(default_factory=lambda: DEFAULT_SCOPES)

    @property
    def subject(self) -> str:
        """Sujeito usado em logs, cotas e rate limit por chave."""
        return f"apikey:{self.fingerprint}"

    def allows(self, scope: str) -> bool:
        return scope in self.scopes


class ApiKeyRegistry:
    """Lê `COMPAT_API_KEYS` e compara tokens em tempo constante."""

    def __init__(self, keys: Sequence[str]) -> None:
        self._entries: list[tuple[str, ApiKeyIdentity]] = []
        seen: set[str] = set()
        for raw in keys:
            key = raw.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            self._entries.append((key, self._identity(key)))

    @classmethod
    def from_settings(cls, settings: Settings) -> ApiKeyRegistry:
        return cls(settings.compat_api_keys)

    @property
    def enabled(self) -> bool:
        return bool(self._entries)

    @property
    def count(self) -> int:
        return len(self._entries)

    def authenticate(self, token: str) -> ApiKeyIdentity | None:
        """`None` quando a chave não confere; compara todas as chaves."""
        match: ApiKeyIdentity | None = None
        for key, identity in self._entries:
            if hmac.compare_digest(key, token):
                match = identity
        return match

    @staticmethod
    def _identity(key: str) -> ApiKeyIdentity:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return ApiKeyIdentity(
            prefix=key[:PREFIX_LENGTH],
            fingerprint=digest[: FINGERPRINT_BYTES * 2],
        )


__all__ = [
    "DEFAULT_SCOPES",
    "FINGERPRINT_BYTES",
    "PREFIX_LENGTH",
    "SCOPE_CHAT",
    "SCOPE_MODELS",
    "ApiKeyIdentity",
    "ApiKeyRegistry",
]

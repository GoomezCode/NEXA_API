"""Autenticação do admin (config) e de usuários de teste (SQLite) — F13-05."""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    Role,
    create_access_token,
    verify_password,
)
from app.core.users import UserService
from app.db.models import ADMIN_ID


@dataclass(frozen=True, slots=True)
class AuthIdentity:
    """Identidade autenticada pronta para emitir o JWT."""

    subject: str
    role: Role
    user_id: str | None = None
    daily_message_limit: int | None = None
    rate_per_min: int | None = None


class AuthService:
    """Admin pela configuração (fallback) e usuários de teste pelo SQLite."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        users: UserService | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._users = users
        self._logger = get_logger("nexa.auth")

    async def authenticate(self, username: str, password: str) -> AuthIdentity:
        """Valida as credenciais e devolve a identidade (RF-AUT-02 a 04, RF-USR-03)."""
        expected_username = self._settings.admin_username.strip().lower()
        username_matches = hmac.compare_digest(
            username.strip().lower().encode("utf-8"), expected_username.encode("utf-8")
        )

        if username_matches:
            # O hash é propositalmente custoso: nunca bloquear o event loop (RNF-DES-03).
            valid = await asyncio.to_thread(
                verify_password, password, self._settings.admin_password_hash_value
            )
            if not valid:
                self._logger.info("admin_login_failed")
                raise ApiError("invalid_credentials")
            return AuthIdentity(
                subject=expected_username,
                role="admin",
                user_id=ADMIN_ID,
            )

        if self._users is None:
            # Sem repositório (testes unitários do admin): não revela usuários.
            await asyncio.to_thread(verify_password, password, DUMMY_PASSWORD_HASH)
            self._logger.info("login_failed")
            raise ApiError("invalid_credentials")

        row = await self._users.authenticate(username, password)
        if row is None:
            self._logger.info("login_failed")
            raise ApiError("invalid_credentials")

        await self._users.mark_login(row.id)
        return AuthIdentity(
            subject=row.id,
            role="user",
            user_id=row.id,
            daily_message_limit=row.daily_message_limit,
            rate_per_min=row.rate_per_min,
        )

    def issue_token(self, identity: AuthIdentity) -> tuple[str, int]:
        """Emite o JWT conforme o papel; admin/usuário têm validade curta."""
        if identity.role == "guest":
            expires_in = self._settings.guest_token_expire_hours * 3600
        else:
            expires_in = self._settings.admin_token_expire_minutes * 60
        token = create_access_token(
            self._settings,
            subject=identity.subject,
            role=identity.role,
            expires_in=expires_in,
            now=self._clock.now(),
        )
        return token, expires_in

    def issue_admin_token(self) -> tuple[str, int]:
        """Cria o token do admin e devolve `(token, expires_in)`."""
        return self.issue_token(
            AuthIdentity(
                subject=self._settings.admin_username.strip().lower(),
                role="admin",
                user_id=ADMIN_ID,
            )
        )

    def issue_user_token(self, user_id: str) -> tuple[str, int]:
        """Cria o token de um usuário de teste com `sub = uid`."""
        return self.issue_token(AuthIdentity(subject=user_id, role="user", user_id=user_id))

    def issue_guest_token(self, gid: str) -> tuple[str, int]:
        """Cria o token do convidado com `sub = gid`."""
        return self.issue_token(AuthIdentity(subject=gid, role="guest"))


__all__ = ["AuthIdentity", "AuthService"]

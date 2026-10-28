"""Autenticação do administrador em tempo constante (RF-AUT-02 a 04)."""

from __future__ import annotations

import asyncio
import hmac

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.core.security import create_access_token, verify_password

# Hash argon2id de uma senha aleatória, usado para igualar o custo de verificação
# quando o usuário informado não existe (RF-AUT-04).
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$qSAMcrNYkIeox0WVWhAVQA$"
    "AxML0bnKCoIY+RBSDMJYq4GTmQjkaKE2/Im5l+vyMpc"
)


class AuthService:
    """Autenticação do único usuário do sistema (RF-AUT-01 a 04)."""

    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._settings = settings
        self._clock = clock
        self._logger = get_logger("nexa.auth")

    async def authenticate(self, username: str, password: str) -> None:
        """Valida as credenciais do admin em tempo constante (RF-AUT-03/04)."""
        expected_username = self._settings.admin_username
        username_matches = hmac.compare_digest(
            username.encode("utf-8"), expected_username.encode("utf-8")
        )
        password_hash = (
            self._settings.admin_password_hash_value if username_matches else _DUMMY_PASSWORD_HASH
        )

        # O hash é propositalmente custoso: nunca bloquear o event loop (RNF-DES-03).
        valid = await asyncio.to_thread(verify_password, password, password_hash)

        if not (username_matches and valid):
            # Evento de segurança sem dados sensíveis (RNF-OBS-04).
            self._logger.info("admin_login_failed")
            raise ApiError("invalid_credentials")

    def issue_admin_token(self) -> tuple[str, int]:
        """Cria o token do admin e devolve `(token, expires_in)`."""
        expires_in = self._settings.admin_token_expire_minutes * 60
        token = create_access_token(
            self._settings,
            subject=self._settings.admin_username,
            role="admin",
            expires_in=expires_in,
            now=self._clock.now(),
        )
        return token, expires_in

    def issue_guest_token(self, gid: str) -> tuple[str, int]:
        """Cria o token do convidado com `sub = gid`."""
        expires_in = self._settings.guest_token_expire_hours * 3600
        token = create_access_token(
            self._settings,
            subject=gid,
            role="guest",
            expires_in=expires_in,
            now=self._clock.now(),
        )
        return token, expires_in


__all__ = ["AuthService"]

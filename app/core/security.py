"""Hash de senha e emissão/validação de JWT (RF-AUT-02 a 06, 10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

import bcrypt
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from jwt import InvalidTokenError

from app.config import Settings
from app.core.errors import ApiError

Role = Literal["admin", "guest"]
VALID_ROLES: frozenset[str] = frozenset({"admin", "guest"})

# Hash argon2id de uma senha aleatória, usado para igualar o custo de verificação
# quando o usuário informado não existe (RF-AUT-04).
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$qSAMcrNYkIeox0WVWhAVQA$"
    "AxML0bnKCoIY+RBSDMJYq4GTmQjkaKE2/Im5l+vyMpc"
)

_PASSWORD_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """Gera o hash argon2id da senha (usado por scripts e testes)."""
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Compara a senha com um hash argon2 ou bcrypt, sem lançar exceções."""
    if password_hash.startswith("$argon2"):
        try:
            return _PASSWORD_HASHER.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
    if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except ValueError:
            return False
    return False


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """Conteúdo relevante de um access token."""

    subject: str
    role: Role


def create_access_token(
    settings: Settings,
    *,
    subject: str,
    role: Role,
    expires_in: int,
    now: datetime,
) -> str:
    """Assina um JWT com `sub`, `role`, `iat` e `exp` (RF-AUT-06)."""
    issued_at = int(now.timestamp())
    payload = {
        "sub": subject,
        "role": role,
        "iat": issued_at,
        "exp": issued_at + expires_in,
    }
    token = jwt.encode(payload, settings.jwt_secret_value, algorithm=settings.jwt_algorithm)
    return token if isinstance(token, str) else token.decode("ascii")


def decode_access_token(
    token: str,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> TokenPayload:
    """Valida assinatura, expiração e papel do token (RF-AUT-10).

    A expiração é comparada com `now` (relógio da aplicação), o que mantém o
    comportamento determinístico nos testes; em produção `now` é o UTC atual.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_value,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "role", "iat", "exp"], "verify_exp": False},
        )
    except InvalidTokenError as exc:
        raise ApiError("invalid_token") from exc

    expires_at = payload.get("exp")
    if type(expires_at) is not int:
        raise ApiError("invalid_token")
    current = now or datetime.now(UTC)
    if expires_at <= int(current.timestamp()):
        raise ApiError("token_expired")

    subject = payload.get("sub")
    role = payload.get("role")
    if not isinstance(subject, str) or not subject:
        raise ApiError("invalid_token")
    if not isinstance(role, str) or role not in VALID_ROLES:
        raise ApiError("invalid_token")
    return TokenPayload(subject=subject, role=cast(Role, role))


__all__ = [
    "VALID_ROLES",
    "Role",
    "TokenPayload",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]

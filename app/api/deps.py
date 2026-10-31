"""Dependências de autenticação/autorização (RF-AUT-07, RF-ADM-01)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import ApiError
from app.core.security import Role, decode_access_token
from app.db.models import ADMIN_ID

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT emitido por `/api/v1/auth/login` ou `/api/v1/auth/guest`.",
)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Identidade autenticada pelo token Bearer."""

    subject: str
    role: Role
    user_id: str | None = None
    daily_message_limit: int | None = None
    rate_per_min: int | None = None


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise ApiError("invalid_token")
    scheme, _, credentials = header.partition(" ")
    token = credentials.strip()
    if scheme.lower() != "bearer" or not token:
        raise ApiError("invalid_token")
    return token


async def get_current_user(
    request: Request,
    _credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> CurrentUser:
    """Valida o token Bearer e devolve o usuário autenticado.

    O admin usa apenas a configuração (RF-USR-03); usuários de teste são
    revalidados no SQLite para que desativação/expiração invalidem o token
    na próxima requisição (RF-USR-14).
    """
    settings: Settings = request.app.state.settings
    clock: Clock = request.app.state.clock
    token = _bearer_token(request)
    payload = decode_access_token(token, settings, now=clock.now())

    if payload.role == "user":
        users = getattr(request.app.state, "user_service", None)
        if users is None:
            raise ApiError("invalid_token")
        row = await users.resolve_token(payload.subject)
        return CurrentUser(
            subject=row.id,
            role="user",
            user_id=row.id,
            daily_message_limit=row.daily_message_limit,
            rate_per_min=row.rate_per_min,
        )

    return CurrentUser(
        subject=payload.subject,
        role=payload.role,
        user_id=ADMIN_ID if payload.role == "admin" else None,
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Restringe a rota ao papel `admin`; guest/usuário recebem `403`."""
    if user.role != "admin":
        raise ApiError("forbidden")
    return user


__all__ = ["CurrentUser", "get_current_user", "require_admin"]

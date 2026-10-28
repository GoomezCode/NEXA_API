"""Dependências de autenticação/autorização (RF-AUT-07, RF-ADM-01)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import ApiError
from app.core.security import Role, decode_access_token

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT emitido por `/api/v1/auth/login` ou `/api/v1/auth/guest`.",
)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Identidade autenticada pelo token Bearer."""

    subject: str
    role: Role


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise ApiError("invalid_token")
    scheme, _, credentials = header.partition(" ")
    token = credentials.strip()
    if scheme.lower() != "bearer" or not token:
        raise ApiError("invalid_token")
    return token


def get_current_user(
    request: Request,
    _credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> CurrentUser:
    """Valida o token Bearer e devolve o usuário autenticado.

    O esquema `HTTPBearer` é declarado apenas para documentar a autenticação no
    OpenAPI (CA-17); a validação real é feita abaixo, com os erros do catálogo.
    """
    settings: Settings = request.app.state.settings
    clock: Clock = request.app.state.clock
    token = _bearer_token(request)
    payload = decode_access_token(token, settings, now=clock.now())
    return CurrentUser(subject=payload.subject, role=payload.role)


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Restringe a rota ao papel `admin`; guest recebe `403`."""
    if user.role != "admin":
        raise ApiError("forbidden")
    return user


__all__ = ["CurrentUser", "get_current_user", "require_admin"]

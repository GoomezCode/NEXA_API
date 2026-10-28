"""Dependências de autenticação/autorização (RF-AUT-07, RF-ADM-01)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import ApiError
from app.core.security import Role, decode_access_token


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


def get_current_user(request: Request) -> CurrentUser:
    """Valida o token Bearer e devolve o usuário autenticado."""
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

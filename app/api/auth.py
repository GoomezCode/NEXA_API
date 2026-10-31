"""Rotas de autenticação: login do admin e emissão de token guest (SRS §3.7.1)."""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import APIRouter, Request, Response, status

from app.config import Settings
from app.core.auth import AuthIdentity, AuthService
from app.core.errors import ApiError
from app.core.ip import get_client_ip
from app.core.logging import get_logger
from app.core.rate_limit import SlidingWindowLimiter
from app.core.usage_store import UsageStore
from app.schemas.auth import GuestTokenResponse, LoginRequest, RegisterRequest, TokenResponse

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticação"])
logger = get_logger("nexa.auth")


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cria uma conta de usuário",
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "Cadastro desabilitado."},
        status.HTTP_409_CONFLICT: {"description": "Usuário ou e-mail já existe."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Limite de cadastro excedido."},
    },
)
async def register(payload: RegisterRequest, request: Request, response: Response) -> TokenResponse:
    """Valida o cadastro, cria a conta e devolve uma sessão de usuário."""
    settings = _settings(request)
    if not settings.allow_self_registration:
        raise ApiError("registration_disabled")

    ip = get_client_ip(request, settings.trusted_proxies)
    limiter = _limiter(request, "registration_limiter")
    allowed = await limiter.acquire(ip)
    if not allowed.allowed:
        raise ApiError("rate_limited", headers={"Retry-After": str(allowed.retry_after)})

    users = cast(Any, request.app.state.user_service)
    row = await users.register_user(payload.username, payload.email, payload.password)
    identity = AuthIdentity(
        subject=row.id,
        role="user",
        user_id=row.id,
        daily_message_limit=row.daily_message_limit,
        rate_per_min=row.rate_per_min,
    )
    token, expires_in = _auth_service(request).issue_token(identity)
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(access_token=token, expires_in=expires_in, role="user", user_id=row.id)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _auth_service(request: Request) -> AuthService:
    return cast(AuthService, request.app.state.auth_service)


def _limiter(request: Request, name: str) -> SlidingWindowLimiter:
    return cast(SlidingWindowLimiter, getattr(request.app.state, name))


def _usage_store(request: Request) -> UsageStore:
    return cast(UsageStore, request.app.state.usage_store)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login do administrador ou usuário de teste",
    description=(
        "Valida usuário e senha (admin pela configuração, usuário de teste pelo SQLite) "
        "e devolve um access token JWT."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Credenciais inválidas."},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Login temporariamente bloqueado."},
    },
)
async def login(payload: LoginRequest, request: Request) -> TokenResponse:
    settings = _settings(request)
    ip = get_client_ip(request, settings.trusted_proxies)
    limiter = _limiter(request, "login_limiter")

    blocked = await limiter.check(ip)
    if not blocked.allowed:
        logger.warning("admin_login_locked", ip=ip, retry_after=blocked.retry_after)
        raise ApiError(
            "login_locked",
            headers={"Retry-After": str(blocked.retry_after)},
        )

    try:
        identity = await _auth_service(request).authenticate(payload.username, payload.password)
    except ApiError:
        await limiter.register_failure(ip)
        raise

    await limiter.reset(ip)
    token, expires_in = _auth_service(request).issue_token(identity)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        role=identity.role,
        user_id=identity.user_id,
    )


@router.post(
    "/guest",
    response_model=GuestTokenResponse,
    summary="Emite token de convidado",
    description=(
        "Cria um `gid` (UUID v4) e devolve um token com papel `guest` e o uso inicial. "
        "A emissão é limitada por IP."
    ),
    responses={
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Limite de emissão por IP excedido."}
    },
)
async def guest_token(request: Request) -> GuestTokenResponse:
    settings = _settings(request)
    ip = get_client_ip(request, settings.trusted_proxies)
    limiter = _limiter(request, "guest_token_limiter")

    allowed = await limiter.acquire(ip)
    if not allowed.allowed:
        logger.warning("guest_token_limit_reached", ip=ip, retry_after=allowed.retry_after)
        raise ApiError(
            "guest_token_limit_reached",
            headers={"Retry-After": str(allowed.retry_after)},
        )

    gid = str(uuid.uuid4())
    token, expires_in = _auth_service(request).issue_guest_token(gid)
    usage = await _usage_store(request).snapshot(role="guest", subject=gid, ip=ip)
    logger.info("guest_token_issued")
    return GuestTokenResponse(
        access_token=token,
        expires_in=expires_in,
        role="guest",
        usage=usage,
    )


__all__ = ["router"]

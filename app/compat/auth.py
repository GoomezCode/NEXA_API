"""Autenticação e limites das rotas de compatibilidade (RF-AUT-11, RF-USO-14/15)."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Literal, cast

from fastapi import Request

from app.compat.errors import CompatApiError
from app.config import Settings
from app.core.api_keys import (
    DEFAULT_SCOPES,
    SCOPE_CHAT,
    SCOPE_MODELS,
    ApiKeyIdentity,
    ApiKeyRegistry,
)
from app.core.clock import Clock
from app.core.errors import ApiError
from app.core.ip import get_client_ip
from app.core.logging import get_logger
from app.core.rate_limit import SlidingWindowLimiter
from app.core.security import Role, decode_access_token
from app.core.stats import UsageStats
from app.core.usage import rate_limit_headers
from app.core.usage_store import UsageStore

logger = get_logger("nexa.compat")

#: Escopos exigidos pelas rotas de compatibilidade.
COMPAT_SCOPES = frozenset({SCOPE_CHAT, SCOPE_MODELS})

IdentityKind = Literal["api_key", "jwt", "anonymous"]


@dataclass(frozen=True, slots=True)
class CompatIdentity:
    """Identidade do chamador em uma rota de compatibilidade."""

    kind: IdentityKind
    subject: str
    role: Role
    scopes: frozenset[str] = field(default_factory=lambda: DEFAULT_SCOPES)
    prefix: str | None = None
    fingerprint: str | None = None
    daily_limit: int | None = None
    rate_per_min: int | None = None


@dataclass(frozen=True, slots=True)
class CompatLimits:
    """Resultado do rate limit/cota, com cabeçalhos a devolver."""

    headers: dict[str, str]
    refundable: bool


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _registry(request: Request) -> ApiKeyRegistry:
    return cast(ApiKeyRegistry, request.app.state.compat_api_keys)


def _token_from_request(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    token = credentials.strip()
    if scheme.lower() == "bearer" and token:
        return token
    api_key = request.headers.get("x-api-key", "").strip()
    return api_key or None


def _peer_is_loopback(request: Request) -> bool:
    """Conexão direta veio de loopback (não considera `X-Forwarded-For`)."""
    peer = request.client.host if request.client else ""
    try:
        return ip_address(peer).is_loopback
    except ValueError:
        return False


def _key_identity(token: str, *, scope: str, request: Request) -> CompatIdentity:
    identity = _registry(request).authenticate(token)
    if identity is None:
        logger.warning("compat_api_key_rejected", prefix=token[:8], scope=scope)
        raise CompatApiError("invalid_api_key")
    return _from_api_key(identity)


def _from_api_key(identity: ApiKeyIdentity) -> CompatIdentity:
    return CompatIdentity(
        kind="api_key",
        subject=identity.subject,
        role="user",
        scopes=identity.scopes,
        prefix=identity.prefix,
        fingerprint=identity.fingerprint,
    )


async def _jwt_identity(token: str, request: Request, *, scope: str) -> CompatIdentity:
    settings = _settings(request)
    clock = cast(Clock, request.app.state.clock)
    try:
        payload = decode_access_token(token, settings, now=clock.now())
    except ApiError as exc:
        logger.warning("compat_jwt_rejected", scope=scope, reason=exc.code)
        raise CompatApiError("invalid_api_key") from exc

    if payload.role != "user":
        return CompatIdentity(
            kind="jwt",
            subject=payload.subject,
            role=payload.role,
            scopes=DEFAULT_SCOPES,
        )

    users = getattr(request.app.state, "user_service", None)
    if users is None:
        raise CompatApiError("invalid_api_key")
    try:
        row = await users.resolve_token(payload.subject)
    except ApiError as exc:
        raise CompatApiError("invalid_api_key") from exc
    return CompatIdentity(
        kind="jwt",
        subject=row.id,
        role="user",
        scopes=DEFAULT_SCOPES,
        daily_limit=row.daily_message_limit,
        rate_per_min=row.rate_per_min,
    )


async def get_compat_identity(
    request: Request,
    *,
    scope: str,
    allow_localhost: bool = False,
) -> CompatIdentity:
    """API key NEXA, JWT admin/usuário ou modo anônimo (somente desenvolvimento).

    `allow_localhost` habilita o acesso sem chave para conexões de loopback,
    usado exclusivamente pela superfície Ollama em desenvolvimento.
    """
    settings = _settings(request)
    token = _token_from_request(request)

    if token is not None and token.startswith("nxa_"):
        identity = _key_identity(token, scope=scope, request=request)
    elif token is not None:
        identity = await _jwt_identity(token, request, scope=scope)
    elif allow_localhost and settings.compat_ollama_allow_localhost and _peer_is_loopback(request):
        ip = get_client_ip(request, settings.trusted_proxies)
        identity = CompatIdentity(
            kind="anonymous",
            subject=f"localhost:{ip}",
            role="guest",
            scopes=DEFAULT_SCOPES,
        )
        logger.info("compat_ollama_localhost_access", scope=scope, ip=ip)
    elif settings.compat_allow_anonymous:
        ip = get_client_ip(request, settings.trusted_proxies)
        identity = CompatIdentity(
            kind="anonymous",
            subject=f"anonymous:{ip}",
            role="guest",
            scopes=DEFAULT_SCOPES,
        )
        logger.warning("compat_anonymous_access", scope=scope, ip=ip)
    else:
        logger.warning("compat_missing_api_key", scope=scope)
        raise CompatApiError("invalid_api_key")

    if not identity.scopes.issuperset({scope}):
        raise CompatApiError("forbidden")
    return identity


async def enforce_compat_limits(
    request: Request,
    identity: CompatIdentity,
    *,
    ip: str,
    stats: UsageStats | None = None,
) -> CompatLimits:
    """Rate limit por API key ou cotas do JWT/anônimo (RF-USO-14/15)."""
    if identity.kind == "api_key":
        limiter = cast(SlidingWindowLimiter, request.app.state.compat_key_limiter)
        status = await limiter.acquire(identity.fingerprint or identity.subject)
        headers = {
            "X-RateLimit-Limit": str(limiter.limit),
            "X-RateLimit-Remaining": str(status.remaining),
            "X-RateLimit-Reset": str(int(status.reset_at)),
        }
        if not status.allowed:
            if stats is not None:
                stats.record_rate_limited()
            raise CompatApiError(
                "rate_limited",
                headers=headers,
                retry_after=status.retry_after,
            )
        return CompatLimits(headers=headers, refundable=False)

    store = cast(UsageStore, request.app.state.usage_store)
    decision = await store.consume(
        role=identity.role,
        subject=identity.subject,
        ip=ip,
        daily_limit=identity.daily_limit,
        rate_limit=identity.rate_per_min,
    )
    headers = rate_limit_headers(decision.usage)
    if not decision.allowed:
        if stats is not None:
            stats.record_rate_limited()
        headers["Retry-After"] = str(decision.retry_after)
        code = "quota_exceeded" if (decision.code or "").endswith("_daily_limit_reached") else None
        raise CompatApiError(
            code or "rate_limited",
            headers=headers,
            retry_after=decision.retry_after,
        )
    return CompatLimits(headers=headers, refundable=True)


async def refund_compat(request: Request, identity: CompatIdentity, *, ip: str) -> None:
    """Devolve a cota consumida quando nada foi entregue ao cliente."""
    if identity.kind == "api_key":
        return
    store = cast(UsageStore, request.app.state.usage_store)
    await store.refund(role=identity.role, subject=identity.subject, ip=ip)


__all__ = [
    "CompatIdentity",
    "CompatLimits",
    "enforce_compat_limits",
    "get_compat_identity",
    "refund_compat",
]

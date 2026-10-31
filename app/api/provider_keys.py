"""CRUD de chaves próprias de provedores."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import asdict
from typing import cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, status

from app.ai.base import ProviderError
from app.ai.factory import build_user_provider
from app.api.deps import CurrentUser, get_current_user
from app.config import Settings
from app.core.errors import ApiError
from app.core.ip import get_client_ip
from app.core.provider_keys import PROVIDERS, ProviderKeyStore
from app.core.rate_limit import SlidingWindowLimiter
from app.db.connection import Database
from app.schemas.provider_keys import ProviderKeyInput, ProviderKeySummary, ProviderKeyTestResult

router = APIRouter(prefix="/api/v1/me/providers", tags=["Chaves de provedores"])


def _store(request: Request) -> ProviderKeyStore:
    return ProviderKeyStore(
        cast(Database, request.app.state.database),
        cast(Settings, request.app.state.settings),
        request.app.state.clock,
    )


async def _allow_request(request: Request, user: CurrentUser) -> None:
    if user.role == "guest" or user.user_id is None:
        raise ApiError("forbidden")
    settings = cast(Settings, request.app.state.settings)
    ip = get_client_ip(request, settings.trusted_proxies)
    limiter: SlidingWindowLimiter = request.app.state.provider_key_limiter
    allowed = await limiter.acquire(f"{user.user_id}:{ip}")
    if not allowed.allowed:
        raise ApiError("rate_limited", headers={"Retry-After": str(allowed.retry_after)})


def _provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ApiError("not_found")
    return provider


def _validate_user_base_url(value: str | None, user: CurrentUser) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ApiError("validation_error")
    if user.role == "admin":
        return value.rstrip("/")
    if parsed.scheme != "https":
        raise ApiError("forbidden")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        if not addresses:
            raise ValueError("host não resolvido")
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise ValueError("host privado")
    except (OSError, ValueError) as exc:
        raise ApiError("validation_error") from exc
    return value.rstrip("/")


@router.get("", response_model=list[ProviderKeySummary], summary="Lista as chaves configuradas")
async def list_provider_keys(
    request: Request, user: CurrentUser = Depends(get_current_user)
) -> list[ProviderKeySummary]:
    """Devolve metadados e os quatro últimos caracteres das chaves."""
    await _allow_request(request, user)
    return [
        ProviderKeySummary(**asdict(item))
        for item in await _store(request).list(user.user_id or "")
    ]


@router.put("/{provider}", response_model=ProviderKeySummary, summary="Salva uma chave de provedor")
async def save_provider_key(
    provider: str,
    payload: ProviderKeyInput,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> ProviderKeySummary:
    """Criptografa e armazena uma chave, sem devolvê-la em texto puro."""
    await _allow_request(request, user)
    provider = _provider(provider)
    base_url = payload.base_url
    if provider == "openai_compatible":
        base_url = _validate_user_base_url(base_url, user)
        if not base_url or not payload.default_model:
            raise ApiError("validation_error")
    elif base_url is not None:
        raise ApiError("validation_error")
    store = _store(request)
    await store.save(user.user_id or "", provider, payload.api_key, base_url, payload.default_model)
    item = next(row for row in await store.list(user.user_id or "") if row.provider == provider)
    return ProviderKeySummary(**asdict(item))


@router.delete(
    "/{provider}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove uma chave de provedor"
)
async def delete_provider_key(
    provider: str, request: Request, user: CurrentUser = Depends(get_current_user)
) -> None:
    """Remove a chave associada ao provedor."""
    await _allow_request(request, user)
    deleted = await _store(request).delete(user.user_id or "", _provider(provider))
    if not deleted:
        raise ApiError("not_found")


@router.post(
    "/{provider}/test", response_model=ProviderKeyTestResult, summary="Testa uma chave salva"
)
async def test_provider_key(
    provider: str, request: Request, user: CurrentUser = Depends(get_current_user)
) -> ProviderKeyTestResult:
    """Valida a chave salva com uma consulta de saúde ao provedor."""
    await _allow_request(request, user)
    provider = _provider(provider)
    store = _store(request)
    key = await store.get(user.user_id or "", provider)
    if key is None:
        raise ApiError("not_found")
    settings = cast(Settings, request.app.state.settings)
    adapter = build_user_provider(
        settings,
        provider=provider,
        api_key=key.api_key,
        base_url=key.base_url,
        default_model=key.default_model,
    )
    try:
        valid = await adapter.health()
    except ProviderError:
        valid = False
    if valid:
        await store.mark_used(user.user_id or "", provider)
    return ProviderKeyTestResult(
        provider=provider,
        valid=valid,
        message="Chave validada."
        if valid
        else "O provedor não aceitou a chave ou está indisponível.",
    )


__all__ = ["router"]

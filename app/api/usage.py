"""Consulta de cotas do chamador (SRS §3.7.2, RF-USO-07)."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUser, get_current_user
from app.config import Settings
from app.core.ip import get_client_ip
from app.core.usage_store import UsageStore
from app.schemas.usage import UsageResponse

router = APIRouter(prefix="/api/v1", tags=["Uso"])


@router.get(
    "/usage",
    response_model=UsageResponse,
    summary="Limites e uso restante",
    description=(
        "Retorna a cota diária (apenas guest), o limite por minuto e o uso "
        "restante do chamador. Para o admin, os campos diários são `null`."
    ),
)
async def get_usage(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> UsageResponse:
    settings = cast(Settings, request.app.state.settings)
    store = cast(UsageStore, request.app.state.usage_store)
    ip = get_client_ip(request, settings.trusted_proxies)
    usage = await store.snapshot(role=user.role, subject=user.subject, ip=ip)
    return UsageResponse(role=user.role, **usage.model_dump())


__all__ = ["router"]

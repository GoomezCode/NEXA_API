"""Endpoints públicos de saúde e prontidão (RF-SIS-01/04/05)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from app import __version__
from app.ai.router import ProviderRouter
from app.core.errors import request_id_of
from app.schemas.system import HealthResponse, ProviderReadiness, ReadinessResponse

router = APIRouter(tags=["Sistema"])

AiHealthCheck = Callable[[], Awaitable[bool]]


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Saúde da API",
    description="Indica que o processo está no ar. Público e sem dependências externas.",
)
async def health(request: Request) -> HealthResponse:
    return HealthResponse(version=__version__, request_id=request_id_of(request))


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Prontidão da API",
    description=(
        "Agrega a disponibilidade dos provedores de IA habilitados e, quando configurado, "
        "do SQLite. Responde `503` se algum componente essencial falhar."
    ),
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(request: Request) -> Response:
    state = request.app.state
    ai_router: ProviderRouter | None = getattr(state, "ai_router", None)
    check: AiHealthCheck | None = getattr(state, "ai_health", None)
    providers: list[ProviderReadiness] = []

    # Um `ai_health` substituído (testes/CA-15) tem precedência sobre o router.
    overridden = check is not None and check != getattr(ai_router, "health", None)

    if ai_router is not None and not overridden:
        for item in await ai_router.list_health():
            providers.append(
                ProviderReadiness(
                    provider=item.provider,
                    available=item.available,
                    error=item.error,
                )
            )
    else:
        available = True
        if check is not None:
            try:
                available = bool(await check())
            except Exception:
                available = False
        providers.append(
            ProviderReadiness(
                provider=cast(str | None, getattr(state, "ai_provider_name", None)) or "unknown",
                available=available,
            )
        )

    healthy = all(item.available for item in providers)
    body = ReadinessResponse(
        status="ok" if healthy else "unavailable",
        provider=cast(str | None, getattr(state, "ai_provider_name", None)),
        providers=providers,
        request_id=request_id_of(request),
    )
    if not healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=body.model_dump(),
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(content=body.model_dump(), headers={"Cache-Control": "no-store"})


__all__ = ["router"]

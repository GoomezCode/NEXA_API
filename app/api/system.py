"""Endpoints públicos de saúde e prontidão (RF-SIS-01/04)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from app import __version__
from app.core.errors import request_id_of
from app.schemas.system import HealthResponse, ReadinessResponse

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
        "Verifica o provedor de IA configurado. Sem provedor registrado (fase de fundação), "
        "responde `200`."
    ),
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(request: Request) -> Response:
    state = request.app.state
    provider = getattr(state, "ai_provider_name", None)
    check: AiHealthCheck | None = getattr(state, "ai_health", None)

    healthy = True
    if check is not None:
        try:
            healthy = bool(await check())
        except Exception:
            healthy = False

    body = ReadinessResponse(
        status="ok" if healthy else "unavailable",
        provider=provider,
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

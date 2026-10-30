"""Endpoints administrativos (SRS §3.7.4, RF-ADM-01 a 05)."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from fastapi import APIRouter, Depends, Request, Response, status

from app.ai.router import ProviderRouter
from app.api.deps import require_admin
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.core.stats import UsageStats
from app.core.usage_store import UsageStore
from app.core.users import UserService
from app.db.connection import Database
from app.schemas.admin import (
    AiStatusResponse,
    GuestEntry,
    GuestsResponse,
    IpUsageEntry,
    ProviderStatus,
    ResetRequest,
    StatsResponse,
)
from app.schemas.users import DbStatusResponse

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["Administração"],
    dependencies=[Depends(require_admin)],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Token ausente ou inválido."}},
)
logger = get_logger("nexa.admin")


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Estatísticas de uso",
    description=(
        "Contadores em memória desde a última inicialização: requisições de chat, "
        "guests distintos, barrados por limite, erros do provedor e duração média."
    ),
    responses={status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."}},
)
async def get_stats(request: Request) -> StatsResponse:
    stats = cast(UsageStats, request.app.state.stats)
    return StatsResponse(**asdict(stats.snapshot()))


@router.get(
    "/db/status",
    response_model=DbStatusResponse,
    summary="Status e latência do SQLite",
    description=(
        "Expõe caminho, tamanho, WAL, versão de migração, contagem de usuários e "
        "latências p95 de leitura/escrita. Nunca retorna conteúdo sensível."
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Banco indisponível."},
    },
)
async def database_status(request: Request) -> DbStatusResponse:
    database: Database | None = getattr(request.app.state, "database", None)
    users = cast(UserService, request.app.state.user_service)
    if database is None or not database.is_open:
        raise ApiError("database_unavailable")
    try:
        total, test_total = await users.stats()
        return DbStatusResponse(
            status="ok",
            path=str(database.path),
            size_bytes=database.file_size(),
            wal=(await database.journal_mode()) == "wal",
            migration_version=await database.migration_version(),
            users_total=total,
            test_users_total=test_total,
            read_latency_ms_p95=database.read_latency_ms_p95,
            write_latency_ms_p95=database.write_latency_ms_p95,
        )
    except ApiError:
        raise
    except Exception as exc:  # pragma: no cover - SQLite inesperado
        logger.warning("database_status_failed")
        raise ApiError("database_unavailable") from exc


@router.get(
    "/ai/status",
    response_model=AiStatusResponse,
    summary="Status dos provedores de IA",
    description=(
        "Verifica a saúde de cada provedor habilitado. Chaves e segredos nunca são expostos."
    ),
    responses={status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."}},
)
async def ai_status(request: Request) -> AiStatusResponse:
    ai_router = cast(ProviderRouter, request.app.state.ai_router)
    health = await ai_router.list_health()
    return AiStatusResponse(
        providers=[
            ProviderStatus(
                provider=item.provider,
                label=item.label,
                default_model=item.default_model,
                available=item.available,
                checked_at=item.checked_at,
                error=item.error,
            )
            for item in health
        ]
    )


@router.get(
    "/guests",
    response_model=GuestsResponse,
    summary="Guests ativos e uso por IP",
    description=(
        "Lista os guests conhecidos em memória (para obter o `gid` usado no reset) "
        "e o uso diário agregado por IP."
    ),
    responses={status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."}},
)
async def list_guests(request: Request) -> GuestsResponse:
    store = cast(UsageStore, request.app.state.usage_store)
    guests = await store.list_guests()
    ip_usage = await store.list_ip_usage()
    return GuestsResponse(
        guests=[
            GuestEntry(
                gid=guest.gid,
                ip=guest.ip,
                daily_used=guest.daily_used,
                daily_limit=guest.daily_limit,
                last_seen_at=guest.last_seen_at,
            )
            for guest in guests
        ],
        ip_usage=[
            IpUsageEntry(
                ip=entry.ip,
                daily_used=entry.daily_used,
                daily_limit=entry.daily_limit,
            )
            for entry in ip_usage
        ],
    )


@router.post(
    "/usage/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Zera a cota de um guest, IP ou usuário",
    description=(
        "Zera a cota diária do `gid`, do IP ou do `uid` informado. Alvo inexistente "
        "retorna `404` (`not_found`)."
    ),
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Cota zerada."},
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_404_NOT_FOUND: {"description": "`gid`, IP ou `uid` sem cota ativa."},
    },
)
async def reset_usage(payload: ResetRequest, request: Request) -> Response:
    store = cast(UsageStore, request.app.state.usage_store)

    reset = await store.reset(scope=payload.scope, value=payload.value)
    if not reset:
        raise ApiError("not_found")

    logger.info("admin_usage_reset", scope=payload.scope, value=payload.value)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]

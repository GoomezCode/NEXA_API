"""Endpoints administrativos (SRS §3.7.4, RF-ADM-01 a 05)."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from fastapi import APIRouter, Depends, Request, Response, status

from app.ai.factory import (
    available_providers,
    build_provider,
    build_registry,
    enabled_provider_names,
    register_provider,
)
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
    ProviderConfig,
    ProviderCreate,
    ProviderListResponse,
    ProviderUpdate,
    ProviderStatus,
    ProviderType,
    ResetRequest,
    StatsResponse,
)
from app.schemas.admin import GuestsResponse
from app.schemas.users import DbStatusResponse

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
    "/providers",
    response_model=ProviderListResponse,
    summary="Lista todos os provedores cadastrados",
    description=(
        "Retorna o catálogo de provedores de IA configurados no registry. "
        "Inclui provedores padrão (ollama, openai, etc.) e quaisquer provedores "
        "adicionados via `POST /admin/providers`."
    ),
    responses={status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."}},
)
async def list_providers(request: Request) -> ProviderListResponse:
    settings: Settings = request.app.state.settings
    providers = enabled_provider_names(settings)
    configs: list[ProviderConfig] = []
    for name in providers:
        try:
            provider = build_provider(settings, name)
            caps = provider.capabilities
            configs.append(
                ProviderConfig(
                    provider=name,
                    default_model=provider.model,
                    available=True,
                    capabilities=[
                        c
                        for c in ("chat", "stream", "tools", "vision", "json_mode")
                        if getattr(caps, c, False)
                    ],
                )
            )
        except Exception as e:
            configs.append(
                ProviderConfig(
                    provider=name,
                    default_model="",
                    available=False,
                    error=str(e),
                )
            )
    return ProviderListResponse(providers=configs)


@router.post(
    "/providers",
    response_model=ProviderConfig,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra um novo provedor de IA",
    description=(
        "Registra um novo provedor no registry global. O provedor ficará "
        "disponível após o próximo reinício da API. "
        "Cuidado: chaves de API nunca são armazenadas em texto puro; apenas "
        "o valor descriptografado é passado ao builder no tempo de execução."
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Provedor inválido ou configuração incompleta."},
    },
)
async def create_provider(payload: ProviderCreate, request: Request) -> ProviderConfig:
    settings: Settings = request.app.state.settings
    try:
        register_provider(
            name=payload.provider,
            builder=lambda s=settings: build_provider(s, payload.provider),
            replace=True,
        )
        provider = build_provider(settings, payload.provider)
        caps = provider.capabilities
        return ProviderConfig(
            provider=payload.provider,
            default_model=provider.model,
            available=True,
            capabilities=[
                c
                for c in ("chat", "stream", "tools", "vision", "json_mode")
                if getattr(caps, c, False)
            ],
        )
    except ProviderConfigurationError as e:
        raise status.HTTP_422_UNPROCESSABLE_ENTITY from e
    except Exception as e:
        raise status.HTTP_422_UNPROCESSABLE_ENTITY from e


@router.patch(
    "/providers/{provider_name}",
    response_model=ProviderConfig,
    summary="Atualiza configuração de um provedor existente",
    description=(
        "Atualiza o modelo padrão e/ou capacidades de um provedor já cadastrado. "
        "Apenas campos fornecidos são alterados; outros mantêm os valores atuais."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Provedor não encontrado."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Configuração inválida."},
    },
)
async def update_provider(
    provider_name: str,
    payload: ProviderUpdate,
    request: Request,
) -> ProviderConfig:
    settings: Settings = request.app.state.settings
    if payload.default_model is not None or payload.capabilities is not None or payload.available is not None:
        try:
            register_provider(
                name=provider_name,
                builder=lambda s=settings: build_provider(s, provider_name),
                replace=True,
            )
            provider = build_provider(settings, provider_name)
            caps = provider.capabilities
            return ProviderConfig(
                provider=provider_name,
                default_model=payload.default_model if payload.default_model is not None else provider.model,
                available=payload.available if payload.available is not None else True,
                capabilities=[
                    c
                    for c in ("chat", "stream", "tools", "vision", "json_mode")
                    if payload.capabilities is None or c in payload.capabilities
                    if getattr(caps, c, False)
                ],
            )
        except ProviderConfigurationError as e:
            raise status.HTTP_422_UNPROCESSABLE_ENTITY from e
    raise status.HTTP_422_UNPROCESSABLE_ENTITY from Exception("Nenhum campo para atualizar")


@router.get(
    "/providers/{provider_name}",
    response_model=ProviderConfig,
    summary="Obtém configuração de um provedor específico",
    description=(
        "Retorna a configuração corrente de um provedor pelo nome. "
        "O `available` field reflete o último health check; segredos nunca são expostos."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Provedor não encontrado."},
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
    },
)
async def get_provider(provider_name: str, request: Request) -> ProviderConfig:
    settings: Settings = request.app.state.settings
    existing = None
    for name in enabled_provider_names(settings):
        if name == provider_name:
            existing = name
            break
    if existing is None:
        raise status.HTTP_404_NOT_FOUND
    try:
        provider = build_provider(settings, provider_name)
        caps = provider.capabilities
        return ProviderConfig(
            provider=provider_name,
            default_model=provider.model,
            available=True,
            capabilities=[
                c
                for c in ("chat", "stream", "tools", "vision", "json_mode")
                if getattr(caps, c, False)
            ],
        )
    except ProviderConfigurationError as e:
        raise status.HTTP_404_NOT_FOUND from e


@router.delete(
    "/providers/{provider_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove um provedor cadastrado",
    description=(
        "Remove um provedor do registry. O provedor padrão não pode ser removido."
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "Requer papel `admin`."},
        status.HTTP_400_BAD_REQUEST: {"description": "Não é possível remover o provedor padrão."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Provedor não encontrado ou removível."},
    },
)
async def delete_provider(provider_name: str, request: Request) -> Response:
    settings: Settings = request.app.state.settings
    default = default_provider_name(settings)
    if provider_name == default:
        raise status.HTTP_400_BAD_REQUEST
    raise status.HTTP_422_UNPROCESSABLE_ENTITY


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

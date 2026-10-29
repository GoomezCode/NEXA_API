"""Catálogo agregado de modelos do gateway (SRS v2.0, RF-GTW-04/09)."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Query, Request, status

from app.ai.base import ProviderError, provider_error_code
from app.ai.capabilities import capability_names, merge_capabilities
from app.ai.router import ProviderRouter, canonical_model_id
from app.api.deps import get_current_user
from app.core.errors import ApiError, request_id_of
from app.schemas.models import (
    ModelEntry,
    ModelIndexEntry,
    ModelsResponse,
    ProviderModels,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["Modelos"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="Lista os modelos dos provedores habilitados",
    description=(
        "Agrega os catálogos de todos os provedores habilitados, com capacidades, "
        "janela de contexto e `selectable` (respeita `AI_ALLOWED_MODELS` e aliases). "
        "Falha de um provedor aparece como `available = false`; falha total retorna "
        "`503 provider_unavailable`. Consultar o catálogo não consome cota."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Token ausente ou inválido."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Resposta inválida do provedor."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Todos os provedores indisponíveis."},
        status.HTTP_504_GATEWAY_TIMEOUT: {"description": "Provedor não respondeu a tempo."},
    },
)
async def list_models(
    request: Request,
    refresh: bool = Query(
        default=False,
        description="Ignora o cache curto e consulta os provedores novamente.",
    ),
) -> ModelsResponse:
    ai_router = cast(ProviderRouter, request.app.state.ai_router)

    try:
        snapshot = await ai_router.list_catalog(
            refresh=refresh,
            request_id=request_id_of(request),
        )
    except ProviderError as exc:
        raise ApiError(provider_error_code(exc)) from exc

    providers: list[ProviderModels] = []
    index: list[ModelIndexEntry] = []

    for entry in snapshot.providers:
        models: list[ModelEntry] = []
        for model in entry.models:
            capabilities = merge_capabilities(entry.capabilities, model)
            model_id = canonical_model_id(entry.provider, model.name)
            selectable = ai_router.is_selectable(entry.provider, model.name)
            models.append(
                ModelEntry(
                    model_id=model_id,
                    name=model.name,
                    size_bytes=model.size_bytes,
                    modified_at=model.modified_at,
                    family=model.family,
                    parameter_size=model.parameter_size,
                    quantization_level=model.quantization_level,
                    capabilities=capability_names(capabilities),
                    context_window=capabilities.context_window,
                    selectable=selectable,
                )
            )
            index.append(
                ModelIndexEntry(
                    model_id=model_id,
                    provider=entry.provider,
                    name=model.name,
                    selectable=selectable,
                )
            )

        providers.append(
            ProviderModels(
                provider=entry.provider,
                name=entry.label,
                available=entry.available,
                default_model=entry.default_model,
                models=models,
            )
        )

    return ModelsResponse(
        default_model=snapshot.default_model_id,
        providers=providers,
        models=index,
    )


__all__ = ["router"]

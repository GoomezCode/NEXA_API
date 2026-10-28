"""Catálogo de modelos do provedor de IA (SRS v1.2, RF-CHAT-13)."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Query, Request, status

from app.ai.base import AIProvider, ProviderError, provider_error_code
from app.api.deps import get_current_user
from app.config import Settings
from app.core.errors import ApiError, request_id_of
from app.core.models import ModelCatalog, is_model_selectable
from app.schemas.models import ModelEntry, ModelsResponse

router = APIRouter(
    prefix="/api/v1",
    tags=["Modelos"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="Lista os modelos instalados",
    description=(
        "Consulta o provedor de IA (Ollama) e devolve os modelos locais instalados, "
        "com nome, tamanho, data de modificação, família, parâmetros e quantização. "
        "`default_model` é o modelo usado quando o chat omite `model`; `selectable` "
        "indica se o modelo pode ser escolhido no chat (respeita `AI_ALLOWED_MODELS`)."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Token ausente ou inválido."},
        status.HTTP_502_BAD_GATEWAY: {"description": "Resposta inválida do provedor."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Provedor de IA indisponível."},
        status.HTTP_504_GATEWAY_TIMEOUT: {"description": "Provedor de IA não respondeu a tempo."},
    },
)
async def list_models(
    request: Request,
    refresh: bool = Query(
        default=False,
        description="Ignora o cache curto e consulta o provedor novamente.",
    ),
) -> ModelsResponse:
    settings = cast(Settings, request.app.state.settings)
    provider = cast(AIProvider, request.app.state.ai_provider)
    catalog = cast(ModelCatalog, request.app.state.model_catalog)

    try:
        models = await catalog.list_models(
            refresh=refresh,
            request_id=request_id_of(request),
        )
    except ProviderError as exc:
        raise ApiError(provider_error_code(exc)) from exc

    return ModelsResponse(
        provider=provider.name,
        default_model=provider.model,
        models=[
            ModelEntry(
                name=model.name,
                size_bytes=model.size_bytes,
                modified_at=model.modified_at,
                family=model.family,
                parameter_size=model.parameter_size,
                quantization_level=model.quantization_level,
                selectable=is_model_selectable(
                    model.name,
                    allowed=settings.ai_allowed_models,
                    default_model=provider.model,
                ),
            )
            for model in models
        ],
    )


__all__ = ["router"]

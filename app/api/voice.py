"""Endpoint de transcrição de voz (áudio processado em memória)."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, get_current_user
from app.config import Settings
from app.core.errors import ApiError
from app.core.rate_limit import SlidingWindowLimiter
from app.voice.stt import transcribe_audio

router = APIRouter(prefix="/api/v1/voice", tags=["Voz"])


class VoiceHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="Disponibilidade configurada da transcrição de voz.")
    stt: bool = Field(description="Se a transcrição está habilitada nesta API.")
    model: str = Field(description="Modelo carregado sob demanda na primeira transcrição.")


class TranscriptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="Texto reconhecido da fala.")


@router.get(
    "/health",
    response_model=VoiceHealthResponse,
    summary="Disponibilidade da transcrição de voz",
    description="Consulta a configuração do STT sem carregar o modelo nem decodificar áudio.",
)
async def voice_health(request: Request) -> VoiceHealthResponse:
    settings: Settings = request.app.state.settings
    return VoiceHealthResponse(
        status="ok" if settings.voice_stt_enabled else "unavailable",
        stt=settings.voice_stt_enabled,
        model=settings.voice_stt_model,
    )


@router.post(
    "/stt",
    response_model=TranscriptionResponse,
    summary="Transcreve fala em português",
    description=(
        "Recebe áudio WAV mono como `audio/wav`, autentica o usuário e executa o Whisper "
        "em uma thread de trabalho. O áudio é decodificado em memória e não é persistido."
    ),
    responses={
        401: {"description": "Token ausente ou inválido."},
        413: {"description": "O áudio excede o limite configurado."},
        415: {"description": "Envie um áudio com o tipo `audio/wav`."},
        429: {"description": "Limite de transcrições excedido."},
        503: {"description": "O serviço de transcrição está indisponível."},
    },
)
async def transcribe(
    request: Request,
    audio: bytes = Body(..., media_type="audio/wav", description="Áudio mono PCM em WAV."),
    user: CurrentUser = Depends(get_current_user),
) -> TranscriptionResponse:
    settings: Settings = request.app.state.settings
    if not settings.voice_stt_enabled:
        raise ApiError("provider_unavailable", detail="A transcrição de voz está desativada.")

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "audio/wav":
        raise ApiError("unsupported_media_type", detail="Envie o áudio no formato WAV.")

    limiter = cast(SlidingWindowLimiter, request.app.state.voice_stt_limiter)
    decision = await limiter.acquire(user.subject)
    if not decision.allowed:
        raise ApiError("rate_limited", headers={"Retry-After": str(decision.retry_after)})

    if not audio:
        raise ApiError("validation_error", detail="O áudio está vazio.")
    if len(audio) > settings.voice_stt_max_audio_bytes:
        raise ApiError("payload_too_large", detail="O áudio excede o limite configurado.")

    try:
        text = await run_in_threadpool(
            transcribe_audio,
            audio,
            model_name=settings.voice_stt_model,
            device=settings.voice_stt_device,
            compute_type=settings.voice_stt_compute_type,
            download_root=settings.voice_stt_model_dir,
            language="pt",
        )
    except ValueError as exc:
        raise ApiError("validation_error", detail=str(exc)) from exc
    except Exception as exc:
        raise ApiError(
            "provider_unavailable", detail="Falha no serviço de transcrição."
        ) from exc

    return TranscriptionResponse(text=text)


__all__ = ["router"]

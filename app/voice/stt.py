"""Reconhecimento de fala em memória com faster-whisper."""

from __future__ import annotations

import io
import threading
from functools import lru_cache
from typing import Any

_model_lock = threading.Lock()
_inference_lock = threading.Lock()


@lru_cache(maxsize=4)
def _load_model(name: str, device: str, compute_type: str, download_root: str) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        name,
        device=device,
        compute_type=compute_type,
        download_root=download_root,
    )


def transcribe_audio(
    raw: bytes,
    *,
    model_name: str,
    device: str,
    compute_type: str,
    download_root: str,
    language: str = "pt",
) -> str:
    """Decodifica WAV e transcreve sem escrever o áudio em disco."""
    import av
    import numpy as np

    try:
        container = av.open(io.BytesIO(raw))
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        frames = []
        for frame in container.decode(audio=0):
            frames.extend(resampler.resample(frame))
        frames.extend(resampler.resample(None))
        if not frames:
            raise ValueError("O arquivo não contém áudio decodificável.")
        samples = np.concatenate([frame.to_ndarray().reshape(-1) for frame in frames]).astype(
            np.float32
        )
    except Exception as exc:
        raise ValueError("O áudio WAV está vazio ou inválido.") from exc

    with _inference_lock:
        with _model_lock:
            whisper = _load_model(model_name, device, compute_type, download_root)
        segments, _ = whisper.transcribe(
            samples,
            language=language,
            vad_filter=True,
            beam_size=5,
            temperature=0,
            condition_on_previous_text=False,
            initial_prompt="Transcrição de conversa falada em português brasileiro.",
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

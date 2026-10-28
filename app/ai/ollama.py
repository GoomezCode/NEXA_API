"""Provedor Ollama: `POST /api/chat` em JSON e NDJSON (SRS §3.1.2)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.ai.base import (
    ChatMessage,
    FinishReason,
    GenerationChunk,
    GenerationResult,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.config import Settings

_CONNECT_TIMEOUT_SECONDS = 10.0
_HEALTH_TIMEOUT_SECONDS = 5.0


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def map_finish_reason(value: object) -> FinishReason:
    """Traduz `done_reason` do Ollama para o contrato da API."""
    if value == "length":
        return "length"
    if value == "timeout":
        return "timeout"
    return "stop"


class OllamaProvider:
    """Implementação do `AIProvider` sobre a API nativa do Ollama."""

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.model = settings.ollama_model
        base = settings.ollama_url.rstrip("/")
        self._chat_url = f"{base}/api/chat"
        self._tags_url = f"{base}/api/tags"

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
    ) -> GenerationResult:
        payload = self._payload(messages, max_tokens=max_tokens, stream=False)
        try:
            async with asyncio.timeout(max_seconds):
                async with httpx.AsyncClient(timeout=self._timeout()) as client:
                    response = await client.post(
                        self._chat_url,
                        json=payload,
                        headers=self._headers(request_id),
                    )
                    response.raise_for_status()
                    data = response.json()
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderTimeout("Ollama não respondeu a tempo.") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Ollama retornou HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Não foi possível conectar ao Ollama.") from exc
        except ValueError as exc:
            raise ProviderError("Resposta inválida do Ollama.") from exc

        return self._parse_generation(data)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
    ) -> AsyncIterator[GenerationChunk]:
        payload = self._payload(messages, max_tokens=max_tokens, stream=True)
        try:
            async with (
                asyncio.timeout(max_seconds),
                httpx.AsyncClient(timeout=self._stream_timeout()) as client,
                client.stream(
                    "POST",
                    self._chat_url,
                    json=payload,
                    headers=self._headers(request_id),
                ) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    raise ProviderError(f"Ollama retornou HTTP {response.status_code}.")
                done_seen = False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk, is_final = self._parse_stream_line(line)
                    if is_final:
                        done_seen = True
                        yield chunk
                        break
                    if chunk.content:
                        yield chunk
                if not done_seen:
                    raise ProviderError("O stream do Ollama terminou sem o evento de conclusão.")
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderTimeout("Ollama não respondeu a tempo.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Não foi possível conectar ao Ollama.") from exc

    async def health(self) -> bool:
        """Disponibilidade do servidor Ollama (sem checar o modelo)."""
        timeout = min(_HEALTH_TIMEOUT_SECONDS, self._settings.ai_timeout_seconds)
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(self._tags_url)
                    return response.status_code == 200
        except (TimeoutError, httpx.HTTPError, ValueError):
            return False

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        stream: bool,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "stream": stream,
            "options": {"num_predict": max_tokens},
        }

    def _headers(self, request_id: str | None) -> dict[str, str]:
        if request_id:
            return {"X-Request-ID": request_id}
        return {}

    def _timeout(self) -> httpx.Timeout:
        total = self._settings.ai_timeout_seconds
        return httpx.Timeout(total, connect=min(_CONNECT_TIMEOUT_SECONDS, total))

    def _stream_timeout(self) -> httpx.Timeout:
        idle = self._settings.ai_stream_idle_timeout_seconds
        return httpx.Timeout(idle, connect=min(_CONNECT_TIMEOUT_SECONDS, idle))

    def _parse_generation(self, data: object) -> GenerationResult:
        if not isinstance(data, dict):
            raise ProviderError("Resposta inválida do Ollama.")
        message = data.get("message")
        if not isinstance(message, dict):
            raise ProviderError("Resposta do Ollama sem mensagem.")
        content = message.get("content")
        if not isinstance(content, str):
            raise ProviderError("Resposta do Ollama sem conteúdo textual.")

        return GenerationResult(
            content=content,
            finish_reason=map_finish_reason(data.get("done_reason")),
            prompt_tokens=_as_int(data.get("prompt_eval_count")),
            completion_tokens=_as_int(data.get("eval_count")),
        )

    def _parse_stream_line(self, line: str) -> tuple[GenerationChunk, bool]:
        try:
            data = json.loads(line)
        except ValueError as exc:
            raise ProviderError("Linha inválida no stream do Ollama.") from exc
        if not isinstance(data, dict):
            raise ProviderError("Linha inválida no stream do Ollama.")

        error = data.get("error")
        if error:
            raise ProviderError(str(error))

        content = ""
        message = data.get("message")
        if isinstance(message, dict):
            raw_content = message.get("content")
            if isinstance(raw_content, str):
                content = raw_content

        is_final = data.get("done") is True
        if not is_final:
            return GenerationChunk(content=content), False

        return (
            GenerationChunk(
                content=content,
                finish_reason=map_finish_reason(data.get("done_reason")),
                prompt_tokens=_as_int(data.get("prompt_eval_count")),
                completion_tokens=_as_int(data.get("eval_count")),
            ),
            True,
        )


__all__ = ["OllamaProvider", "map_finish_reason"]

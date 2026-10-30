"""Provedor Ollama: `POST /api/chat` em JSON e NDJSON (SRS §3.1.2)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.ai.base import (
    Capabilities,
    ChatMessage,
    FinishReason,
    GenerationChunk,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
    ToolSpec,
)
from app.config import Settings

_CONNECT_TIMEOUT_SECONDS = 10.0
_HEALTH_TIMEOUT_SECONDS = 5.0


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _as_size(value: object) -> int | None:
    size = _as_int(value)
    return size if size is not None and size >= 0 else None


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def parse_modified_at(value: object) -> datetime | None:
    """Converte o `modified_at` do Ollama em UTC; datas inválidas viram `None`."""
    text = _as_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_models(data: object) -> list[ModelInfo]:
    """Normaliza `GET /api/tags`; entradas sem nome são ignoradas."""
    if not isinstance(data, dict):
        raise ProviderError("Resposta inválida do Ollama.")
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        raise ProviderError("Resposta do Ollama sem a lista de modelos.")

    models: list[ModelInfo] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        if name is None or not name.strip():
            continue
        details = item.get("details")
        if not isinstance(details, dict):
            details = {}
        models.append(
            ModelInfo(
                name=name,
                size_bytes=_as_size(item.get("size")),
                modified_at=parse_modified_at(item.get("modified_at")),
                family=_as_text(details.get("family")),
                parameter_size=_as_text(details.get("parameter_size")),
                quantization_level=_as_text(details.get("quantization_level")),
            )
        )
    models.sort(key=lambda model: model.name)
    return models


def map_finish_reason(value: object) -> FinishReason:
    """Traduz `done_reason` do Ollama para o contrato da API."""
    if value == "length":
        return "length"
    if value == "timeout":
        return "timeout"
    return "stop"


class OllamaProvider:
    """Implementação do `AIProvider` sobre a API nativa do Ollama.

    O mesmo adapter atende o Ollama local e o Ollama Cloud: o que muda é a base
    URL, a chave (Bearer) e o modelo padrão (RF-GTW-01, F12-05).
    """

    capabilities = Capabilities(
        chat=True,
        stream=True,
        tools=True,
        vision=True,
        json_mode=True,
        context_window=None,
    )

    def __init__(
        self,
        settings: Settings,
        *,
        name: str = "ollama",
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._settings = settings
        self.name = name
        self.model = default_model or settings.ollama_model
        base = (base_url or settings.ollama_url).rstrip("/")
        self._api_key = api_key
        self._chat_url = f"{base}/api/chat"
        self._tags_url = f"{base}/api/tags"
        self._show_url = f"{base}/api/show"
        self._ps_url = f"{base}/api/ps"

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        model: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> GenerationResult:
        payload = self._payload(
            messages,
            max_tokens=max_tokens,
            stream=False,
            model=model or self.model,
            tools=tools,
            tool_choice=tool_choice,
        )
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
        except TimeoutError:
            # `max_seconds` é o teto de geração definido pela API: terminar
            # normalmente com finish_reason=timeout (RF-CHAT-12/CA-21).
            return GenerationResult(content="", finish_reason="timeout")
        except httpx.TimeoutException as exc:
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
        model: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[GenerationChunk]:
        payload = self._payload(
            messages,
            max_tokens=max_tokens,
            stream=True,
            model=model or self.model,
            tools=tools,
            tool_choice=tool_choice,
        )
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
        except TimeoutError:
            # Teto de geração: encerra o stream normalmente (CA-21).
            yield GenerationChunk(finish_reason="timeout")
        except httpx.TimeoutException as exc:
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

    async def list_models(self, *, request_id: str | None = None) -> list[ModelInfo]:
        """Lista os modelos instalados via `GET /api/tags` (RF-CHAT-13)."""
        timeout = min(_HEALTH_TIMEOUT_SECONDS, self._settings.ai_timeout_seconds)
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(self._tags_url, headers=self._headers(request_id))
                    response.raise_for_status()
                    data = response.json()
        except TimeoutError as exc:
            raise ProviderTimeout("Ollama não respondeu a tempo.") from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Ollama não respondeu a tempo.") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Ollama retornou HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Não foi possível conectar ao Ollama.") from exc
        except ValueError as exc:
            raise ProviderError("Resposta inválida do Ollama.") from exc
        return parse_models(data)

    async def count_tokens(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
    ) -> int | None:
        """Ollama não expõe contagem de tokens antes da geração."""
        return None

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        stream: bool,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [_message_payload(item) for item in messages],
            "stream": stream,
            "options": {"num_predict": max_tokens},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": dict(tool.parameters),
                    },
                }
                for tool in tools
            ]
        return payload

    def _headers(self, request_id: str | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if request_id:
            headers["X-Request-ID"] = request_id
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

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

        tool_calls = parse_tool_calls(message.get("tool_calls"))
        finish_reason = map_finish_reason(data.get("done_reason"))
        if tool_calls and finish_reason == "stop":
            finish_reason = "tool_use"
        return GenerationResult(
            content=content,
            finish_reason=finish_reason,
            prompt_tokens=_as_int(data.get("prompt_eval_count")),
            completion_tokens=_as_int(data.get("eval_count")),
            tool_calls=tool_calls,
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
        tool_calls: tuple[ToolCall, ...] = ()
        message = data.get("message")
        if isinstance(message, dict):
            raw_content = message.get("content")
            if isinstance(raw_content, str):
                content = raw_content
            tool_calls = parse_tool_calls(message.get("tool_calls"))

        is_final = data.get("done") is True
        if not is_final:
            return GenerationChunk(content=content, tool_calls=tool_calls), False

        finish_reason = map_finish_reason(data.get("done_reason"))
        if tool_calls and finish_reason == "stop":
            finish_reason = "tool_use"
        return (
            GenerationChunk(
                content=content,
                finish_reason=finish_reason,
                prompt_tokens=_as_int(data.get("prompt_eval_count")),
                completion_tokens=_as_int(data.get("eval_count")),
                tool_calls=tool_calls,
            ),
            True,
        )


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    """Mensagem no formato do Ollama, incluindo chamadas de ferramenta."""
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {"function": {"name": call.name, "arguments": dict(call.arguments)}}
            for call in message.tool_calls
        ]
    return payload


def parse_tool_calls(raw: object) -> tuple[ToolCall, ...]:
    """Normaliza `tool_calls` do Ollama; entradas inválidas são ignoradas."""
    if not isinstance(raw, list):
        return ()
    calls: list[ToolCall] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = _as_text(function.get("name"))
        if not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCall(
                id=_as_text(item.get("id")) or f"call_{index}",
                name=name,
                arguments=arguments,
            )
        )
    return tuple(calls)


__all__ = ["OllamaProvider", "map_finish_reason", "parse_models", "parse_tool_calls"]

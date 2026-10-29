"""Adapter Google Gemini: `generateContent` e streaming SSE (SRS §3.1.2)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import quote

from app.ai.base import (
    Capabilities,
    ChatMessage,
    FinishReason,
    GenerationChunk,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ToolCall,
    ToolSpec,
)
from app.ai.transport import (
    HEALTH_TIMEOUT_SECONDS,
    get_json,
    is_available,
    iter_sse_data,
    post_json,
    request_headers,
)
from app.config import Settings

_GEMINI_CAPABILITIES = Capabilities(
    chat=True,
    stream=True,
    tools=True,
    vision=True,
    json_mode=True,
    context_window=None,
)


def map_finish_reason(value: object) -> FinishReason:
    """Traduz `finishReason` do Gemini para o contrato interno."""
    if value == "MAX_TOKENS":
        return "length"
    return "stop"


def _split_system(
    messages: Sequence[ChatMessage],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Separa `system` e converte mensagens para `contents` do Gemini."""
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    tool_names: dict[str, str] = {}

    for message in messages:
        if message.role == "system":
            if message.content:
                system_parts.append(message.content)
            continue

        if message.role == "tool":
            name = tool_names.get(message.tool_call_id or "", message.tool_call_id or "tool")
            converted.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": {"result": message.content},
                            }
                        }
                    ],
                }
            )
            continue

        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        for call in message.tool_calls:
            parts.append({"functionCall": {"name": call.name, "args": dict(call.arguments)}})
            tool_names[call.id] = call.name

        converted.append(
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": parts or [{"text": ""}],
            }
        )

    system = "\n\n".join(system_parts) if system_parts else None
    return system, converted


def _tool_payload(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": dict(tool.parameters) or {"type": "object", "properties": {}},
                }
                for tool in tools
            ]
        }
    ]


def _tool_config(tool_choice: str | None) -> dict[str, Any] | None:
    if not tool_choice:
        return None
    if tool_choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    if tool_choice in {"required", "any"}:
        return {"functionCallingConfig": {"mode": "ANY"}}
    if tool_choice == "auto":
        return {"functionCallingConfig": {"mode": "AUTO"}}
    return {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": [tool_choice],
        }
    }


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _parse_calls(parts: Sequence[object]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        function_call = part.get("functionCall")
        if not isinstance(function_call, dict):
            continue
        name = function_call.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function_call.get("args")
        calls.append(
            ToolCall(
                id=f"call_{len(calls)}",
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


class GoogleProvider:
    """`AIProvider` sobre a API Generative Language do Google."""

    capabilities = _GEMINI_CAPABILITIES

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.name = "google"
        self.label = "Google Gemini"
        self.model = settings.google_default_model
        base = settings.google_base_url.rstrip("/")
        self._api_root = base if base.endswith("/v1beta") else f"{base}/v1beta"
        self._models_url = f"{self._api_root}/models"
        self._api_key = settings.google_api_key_value

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
            model=model or self.model,
            tools=tools,
            tool_choice=tool_choice,
        )
        try:
            data = await post_json(
                self._generate_url(model or self.model),
                payload,
                headers=self._headers(request_id),
                timeout=self._settings.ai_timeout_seconds,
                max_seconds=max_seconds,
                label=self.label,
            )
        except TimeoutError:
            return GenerationResult(content="", finish_reason="timeout")
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
            model=model or self.model,
            tools=tools,
            tool_choice=tool_choice,
        )
        pending: dict[int, dict[str, Any]] = {}
        finished = False
        try:
            async for raw in iter_sse_data(
                self._stream_url(model or self.model),
                payload,
                headers=self._headers(request_id),
                idle_timeout=self._settings.ai_stream_idle_timeout_seconds,
                max_seconds=max_seconds,
                label=self.label,
            ):
                chunk, is_final = self._parse_stream_event(raw, pending)
                if chunk is not None:
                    yield chunk
                if is_final:
                    finished = True
                    break
        except TimeoutError:
            yield GenerationChunk(finish_reason="timeout")
            return
        if not finished:
            raise ProviderError("O stream do Google terminou sem o evento de conclusão.")

    async def health(self) -> bool:
        return await is_available(
            self._models_url,
            headers=self._headers(None),
            timeout=min(HEALTH_TIMEOUT_SECONDS, self._settings.ai_timeout_seconds),
        )

    async def list_models(self, *, request_id: str | None = None) -> list[ModelInfo]:
        timeout = min(HEALTH_TIMEOUT_SECONDS, self._settings.ai_timeout_seconds)
        data = await get_json(
            self._models_url,
            headers=self._headers(request_id),
            timeout=timeout,
            label=self.label,
        )
        return parse_models(data)

    async def count_tokens(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
    ) -> int | None:
        system, converted = _split_system(messages)
        payload: dict[str, Any] = {"contents": converted}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        try:
            data = await post_json(
                self._count_url(model or self.model),
                payload,
                headers=self._headers(None),
                timeout=min(HEALTH_TIMEOUT_SECONDS, self._settings.ai_timeout_seconds),
                label=self.label,
            )
        except ProviderError:
            return None
        if not isinstance(data, dict):
            return None
        return _as_int(data.get("totalTokens"))

    # --- internos ---------------------------------------------------------
    def _payload(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        system, converted = _split_system(messages)
        payload: dict[str, Any] = {
            "contents": converted,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = _tool_payload(tools)
            config = _tool_config(tool_choice)
            if config is not None:
                payload["toolConfig"] = config
        return payload

    def _generate_url(self, model: str) -> str:
        return f"{self._api_root}/models/{quote(model, safe='')}:generateContent"

    def _stream_url(self, model: str) -> str:
        return f"{self._api_root}/models/{quote(model, safe='')}:streamGenerateContent?alt=sse"

    def _count_url(self, model: str) -> str:
        return f"{self._api_root}/models/{quote(model, safe='')}:countTokens"

    def _headers(self, request_id: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["x-goog-api-key"] = self._api_key
        return request_headers(headers, request_id)

    def _parse_generation(self, data: object) -> GenerationResult:
        candidate = _first_candidate(data)
        assert candidate is not None  # `required=True` garante candidato ou exceção
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            parts = []

        texts: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)

        tool_calls = _parse_calls(parts)
        finish_reason = map_finish_reason(candidate.get("finishReason"))
        if tool_calls and finish_reason == "stop":
            finish_reason = "tool_use"

        usage = data.get("usageMetadata") if isinstance(data, dict) else None
        if not isinstance(usage, dict):
            usage = {}
        return GenerationResult(
            content="".join(texts),
            finish_reason=finish_reason,
            prompt_tokens=_as_int(usage.get("promptTokenCount")),
            completion_tokens=_as_int(usage.get("candidatesTokenCount")),
            tool_calls=tool_calls,
        )

    def _parse_stream_event(
        self,
        raw: str,
        pending: dict[int, dict[str, Any]],
    ) -> tuple[GenerationChunk | None, bool]:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("Evento inválido no stream do Google.") from exc
        if not isinstance(data, dict):
            raise ProviderError("Evento inválido no stream do Google.")

        error = data.get("error")
        if isinstance(error, dict):
            raise ProviderError(str(error.get("message") or "erro do Google"))

        candidate = _first_candidate(data, required=False)
        if candidate is None:
            return None, False

        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            parts = []

        texts: list[str] = []
        new_call = False
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                name = function_call.get("name")
                if isinstance(name, str) and name:
                    pending[len(pending)] = {
                        "id": f"call_{len(pending)}",
                        "name": name,
                        "arguments": function_call.get("args")
                        if isinstance(function_call.get("args"), dict)
                        else {},
                    }
                    new_call = True

        finish_raw = candidate.get("finishReason")
        usage = data.get("usageMetadata")
        if not isinstance(usage, dict):
            usage = {}

        if finish_raw is None:
            if not texts and not new_call:
                return None, False
            return (
                GenerationChunk(
                    content="".join(texts),
                    tool_calls=_pending_calls(pending) if new_call else (),
                ),
                False,
            )

        tool_calls = _pending_calls(pending)
        finish_reason = map_finish_reason(finish_raw)
        if tool_calls and finish_reason == "stop":
            finish_reason = "tool_use"
        return (
            GenerationChunk(
                content="".join(texts),
                finish_reason=finish_reason,
                prompt_tokens=_as_int(usage.get("promptTokenCount")),
                completion_tokens=_as_int(usage.get("candidatesTokenCount")),
                tool_calls=tool_calls,
            ),
            True,
        )


def _first_candidate(data: object, *, required: bool = True) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        if required:
            raise ProviderError("Resposta inválida do Google.")
        return None
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        if required:
            raise ProviderError("Resposta do Google sem candidatos.")
        return None
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        if required:
            raise ProviderError("Resposta do Google sem candidatos.")
        return None
    return candidate


def _pending_calls(pending: dict[int, dict[str, Any]]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for index, slot in sorted(pending.items()):
        if not slot.get("name"):
            continue
        arguments = slot.get("arguments")
        calls.append(
            ToolCall(
                id=str(slot.get("id") or f"call_{index}"),
                name=str(slot.get("name") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


def parse_models(data: object) -> list[ModelInfo]:
    """Normaliza `GET /v1beta/models` do Gemini."""
    if not isinstance(data, dict):
        raise ProviderError("Resposta inválida do Google.")
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        raise ProviderError("Resposta do Google sem a lista de modelos.")

    models: list[ModelInfo] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.removeprefix("models/")
        methods = item.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            continue

        context_window = _as_int(item.get("inputTokenLimit"))
        capabilities = Capabilities(
            chat=True,
            stream=True,
            tools=True,
            vision=True,
            json_mode=True,
            context_window=context_window,
        )
        models.append(
            ModelInfo(
                name=name,
                family="gemini",
                capabilities=capabilities,
                context_window=context_window,
            )
        )
    models.sort(key=lambda model: model.name)
    return models


__all__ = ["GoogleProvider", "map_finish_reason", "parse_models"]

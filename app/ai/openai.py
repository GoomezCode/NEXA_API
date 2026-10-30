"""Adapter OpenAI: `/chat/completions` em JSON e SSE (SRS §3.1.2)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

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


def map_finish_reason(value: object) -> FinishReason:
    """Traduz `finish_reason` da OpenAI para o contrato interno."""
    if value == "length":
        return "length"
    if value in {"tool_calls", "function_call"}:
        return "tool_use"
    return "stop"


def _tool_calls(raw: object) -> tuple[ToolCall, ...]:
    if not isinstance(raw, list):
        return ()
    calls: list[ToolCall] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = item.get("id")
        calls.append(
            ToolCall(
                id=call_id if isinstance(call_id, str) and call_id else f"call_{index}",
                name=name,
                arguments=arguments,
            )
        )
    return tuple(calls)


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(dict(call.arguments)),
                },
            }
            for call in message.tool_calls
        ]
    if message.role == "tool" and message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _tool_payload(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
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


class OpenAIProvider:
    """`AIProvider` sobre a API de chat da OpenAI e de provedores compatíveis."""

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
        name: str = "openai",
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        organization: str | None = None,
        label: str = "OpenAI",
    ) -> None:
        self._settings = settings
        self.name = name
        self.label = label
        self.model = default_model or settings.openai_default_model
        base = (base_url or settings.openai_base_url).rstrip("/")
        self._chat_url = f"{base}/chat/completions"
        self._models_url = f"{base}/models"
        self._api_key = api_key
        self._organization = organization

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
            data = await post_json(
                self._chat_url,
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
            stream=True,
            model=model or self.model,
            tools=tools,
            tool_choice=tool_choice,
        )
        pending: dict[int, dict[str, Any]] = {}
        finished = False
        try:
            async for raw in iter_sse_data(
                self._chat_url,
                payload,
                headers=self._headers(request_id),
                idle_timeout=self._settings.ai_stream_idle_timeout_seconds,
                max_seconds=max_seconds,
                label=self.label,
            ):
                if raw == "[DONE]":
                    finished = True
                    break
                chunk = self._parse_stream_chunk(raw, pending)
                if chunk is None:
                    continue
                if chunk.finish_reason is not None:
                    finished = True
                yield chunk
        except TimeoutError:
            yield GenerationChunk(finish_reason="timeout")
            return
        if not finished:
            raise ProviderError(f"O stream do {self.label} terminou sem o evento de conclusão.")

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
        return parse_models(data, label=self.label)

    async def count_tokens(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
    ) -> int | None:
        return None

    # --- internos ---------------------------------------------------------
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
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = _tool_payload(tools)
            payload["tool_choice"] = tool_choice or "auto"
        return payload

    def _headers(self, request_id: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        return request_headers(headers, request_id)

    def _parse_generation(self, data: object) -> GenerationResult:
        if not isinstance(data, dict):
            raise ProviderError(f"Resposta inválida do {self.label}.")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(f"Resposta do {self.label} sem escolhas.")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError(f"Resposta do {self.label} sem escolhas.")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError(f"Resposta do {self.label} sem mensagem.")

        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ProviderError(f"Resposta do {self.label} sem conteúdo textual.")

        tool_calls = _tool_calls(message.get("tool_calls"))
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        return GenerationResult(
            content=content,
            finish_reason=map_finish_reason(choice.get("finish_reason")),
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            tool_calls=tool_calls,
        )

    def _parse_stream_chunk(
        self,
        raw: str,
        pending: dict[int, dict[str, Any]],
    ) -> GenerationChunk | None:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"Evento inválido no stream do {self.label}.") from exc
        if not isinstance(data, dict):
            raise ProviderError(f"Evento inválido no stream do {self.label}.")

        error = data.get("error")
        if isinstance(error, dict):
            raise ProviderError(str(error.get("message") or "erro do provedor"))

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, dict):
            return None
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}

        self._accumulate_tool_calls(delta.get("tool_calls"), pending)

        content = delta.get("content")
        if not isinstance(content, str):
            content = ""
        finish_reason = choice.get("finish_reason")
        if finish_reason is None:
            return GenerationChunk(content=content) if content else None
        return GenerationChunk(
            content=content,
            finish_reason=map_finish_reason(finish_reason),
            tool_calls=_pending_calls(pending),
        )

    @staticmethod
    def _accumulate_tool_calls(raw: object, pending: dict[int, dict[str, Any]]) -> None:
        if not isinstance(raw, list):
            return
        for position, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            key = index if isinstance(index, int) else position
            slot = pending.setdefault(key, {"id": "", "name": "", "arguments": ""})
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                slot["id"] = call_id
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                slot["name"] = name
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                slot["arguments"] += arguments


def _pending_calls(pending: dict[int, dict[str, Any]]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for index, slot in sorted(pending.items()):
        name = slot.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw_arguments = slot.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except ValueError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCall(
                id=str(slot.get("id") or f"call_{index}"),
                name=name,
                arguments=arguments,
            )
        )
    return tuple(calls)


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def parse_models(data: object, *, label: str = "OpenAI") -> list[ModelInfo]:
    """Normaliza `GET /models` (`{"data": [{"id": ...}]}`)."""
    if not isinstance(data, dict):
        raise ProviderError(f"Resposta inválida do {label}.")
    raw_models = data.get("data")
    if not isinstance(raw_models, list):
        raise ProviderError(f"Resposta do {label} sem a lista de modelos.")

    models: list[ModelInfo] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        name = item.get("id")
        if not isinstance(name, str) or not name.strip():
            continue
        created = item.get("created")
        modified_at = None
        if isinstance(created, int) and not isinstance(created, bool):
            modified_at = datetime.fromtimestamp(created, tz=UTC)
        models.append(ModelInfo(name=name, modified_at=modified_at))
    models.sort(key=lambda model: model.name)
    return models


__all__ = ["OpenAIProvider", "map_finish_reason", "parse_models"]

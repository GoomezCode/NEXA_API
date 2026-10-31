"""Adapter Anthropic: Messages API em JSON e SSE (SRS §3.1.2)."""

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


def map_stop_reason(value: object) -> FinishReason:
    """Traduz `stop_reason` da Anthropic para o contrato interno."""
    if value == "max_tokens":
        return "length"
    if value == "tool_use":
        return "tool_use"
    return "stop"


def _content_blocks(message: ChatMessage) -> list[dict[str, Any]]:
    """Converte uma mensagem interna em blocos de conteúdo da Anthropic."""
    if message.role == "tool":
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": message.tool_call_id or "",
            "content": message.content,
        }
        return [block]

    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls:
        blocks.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": dict(call.arguments),
            }
        )
    return blocks or [{"type": "text", "text": ""}]


def _split_system(
    messages: Sequence[ChatMessage],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Separa mensagens `system` (campo próprio) das demais."""
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            if message.content:
                system_parts.append(message.content)
            continue
        converted.append(
            {
                "role": "assistant" if message.role == "assistant" else "user",
                "content": _content_blocks(message),
            }
        )
    system = "\n\n".join(system_parts) if system_parts else None
    return system, converted


def _tool_payload(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": dict(tool.parameters) or {"type": "object", "properties": {}},
        }
        for tool in tools
    ]


class AnthropicProvider:
    """`AIProvider` sobre a Messages API da Anthropic (Claude)."""

    capabilities = Capabilities(
        chat=True,
        stream=True,
        tools=True,
        vision=True,
        json_mode=False,
        context_window=None,
    )

    def __init__(
        self,
        settings: Settings,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._settings = settings
        self.name = "anthropic"
        self.label = "Anthropic"
        self.model = default_model or settings.anthropic_default_model
        base = (base_url or settings.anthropic_base_url).rstrip("/")
        self._messages_url = f"{base}/v1/messages"
        self._count_url = f"{base}/v1/messages/count_tokens"
        self._models_url = f"{base}/v1/models"
        self._api_key = api_key if api_key is not None else settings.anthropic_api_key_value
        self._version = settings.anthropic_version

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
                self._messages_url,
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
        blocks: dict[int, dict[str, Any]] = {}
        finished = False
        try:
            async for raw in iter_sse_data(
                self._messages_url,
                payload,
                headers=self._headers(request_id),
                idle_timeout=self._settings.ai_stream_idle_timeout_seconds,
                max_seconds=max_seconds,
                label=self.label,
            ):
                chunk, is_final = self._parse_stream_event(raw, blocks)
                if chunk is not None:
                    yield chunk
                if is_final:
                    finished = True
                    break
        except TimeoutError:
            yield GenerationChunk(finish_reason="timeout")
            return
        if not finished:
            raise ProviderError("O stream da Anthropic terminou sem o evento de conclusão.")

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
        payload: dict[str, Any] = {"model": model or self.model, "messages": converted}
        if system:
            payload["system"] = system
        try:
            data = await post_json(
                self._count_url,
                payload,
                headers=self._headers(None),
                timeout=min(HEALTH_TIMEOUT_SECONDS, self._settings.ai_timeout_seconds),
                label=self.label,
            )
        except ProviderError:
            return None
        if not isinstance(data, dict):
            return None
        value = data.get("input_tokens")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

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
        system, converted = _split_system(messages)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": converted,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = _tool_payload(tools)
            if tool_choice:
                payload["tool_choice"] = {"type": tool_choice}
        return payload

    def _headers(self, request_id: str | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self._version,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return request_headers(headers, request_id)

    def _parse_generation(self, data: object) -> GenerationResult:
        if not isinstance(data, dict):
            raise ProviderError("Resposta inválida da Anthropic.")
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderError("Resposta da Anthropic sem blocos de conteúdo.")

        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    texts.append(text)
            elif block_type == "tool_use":
                name = block.get("name")
                if not isinstance(name, str) or not name:
                    continue
                arguments = block.get("input")
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"call_{len(calls)}"),
                        name=name,
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )

        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        return GenerationResult(
            content="".join(texts),
            finish_reason=map_stop_reason(data.get("stop_reason")),
            prompt_tokens=_as_int(usage.get("input_tokens")),
            completion_tokens=_as_int(usage.get("output_tokens")),
            tool_calls=tuple(calls),
        )

    def _parse_stream_event(
        self,
        raw: str,
        blocks: dict[int, dict[str, Any]],
    ) -> tuple[GenerationChunk | None, bool]:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("Evento inválido no stream da Anthropic.") from exc
        if not isinstance(data, dict):
            raise ProviderError("Evento inválido no stream da Anthropic.")

        event_type = data.get("type")
        if event_type == "error":
            error = data.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            raise ProviderError(str(message or "erro da Anthropic"))

        if event_type == "content_block_start":
            index = data.get("index")
            block = data.get("content_block")
            if (
                isinstance(index, int)
                and isinstance(block, dict)
                and block.get("type") == "tool_use"
            ):
                blocks[index] = {
                    "id": block.get("id") or f"call_{index}",
                    "name": block.get("name") or "",
                    "arguments": "",
                }
            return None, False

        if event_type == "content_block_delta":
            index = data.get("index")
            delta = data.get("delta")
            if not isinstance(delta, dict):
                return None, False
            if delta.get("type") == "text_delta":
                text = delta.get("text")
                if isinstance(text, str) and text:
                    return GenerationChunk(content=text), False
                return None, False
            if delta.get("type") == "input_json_delta" and isinstance(index, int):
                partial = delta.get("partial_json")
                slot = blocks.get(index)
                if slot is not None and isinstance(partial, str):
                    slot["arguments"] += partial
            return None, False

        if event_type == "message_delta":
            delta = data.get("delta")
            stop_reason = delta.get("stop_reason") if isinstance(delta, dict) else None
            usage = data.get("usage")
            output_tokens = _as_int(usage.get("output_tokens")) if isinstance(usage, dict) else None
            return (
                GenerationChunk(
                    finish_reason=map_stop_reason(stop_reason),
                    completion_tokens=output_tokens,
                    tool_calls=_pending_calls(blocks),
                ),
                False,
            )

        if event_type == "message_stop":
            return None, True

        return None, False


def _pending_calls(blocks: dict[int, dict[str, Any]]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for index, slot in sorted(blocks.items()):
        name = slot.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw_arguments = slot.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except ValueError:
            arguments = {}
        calls.append(
            ToolCall(
                id=str(slot.get("id") or f"call_{index}"),
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def parse_models(data: object) -> list[ModelInfo]:
    """Normaliza `GET /v1/models` da Anthropic."""
    if not isinstance(data, dict):
        raise ProviderError("Resposta inválida da Anthropic.")
    raw_models = data.get("data")
    if not isinstance(raw_models, list):
        raise ProviderError("Resposta da Anthropic sem a lista de modelos.")

    models: list[ModelInfo] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        name = item.get("id")
        if not isinstance(name, str) or not name.strip():
            continue
        modified_at = None
        created_at = item.get("created_at")
        if isinstance(created_at, str):
            try:
                modified_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if modified_at.tzinfo is None:
                    modified_at = modified_at.replace(tzinfo=UTC)
            except ValueError:
                modified_at = None
        models.append(ModelInfo(name=name, modified_at=modified_at, family="claude"))
    models.sort(key=lambda model: model.name)
    return models


__all__ = ["AnthropicProvider", "map_stop_reason", "parse_models"]

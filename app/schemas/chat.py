"""Schemas do chat (SRS v2.0, §3.7.3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.base import FinishReason, MessageRole
from app.schemas.usage import UsageInfo


class ChatMessageInput(BaseModel):
    """Mensagem enviada pelo cliente."""

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str
    tool_call_id: str | None = Field(default=None, description="ID da chamada respondida.")

    @field_validator("content")
    @classmethod
    def _content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("não pode ser vazio")
        return value


class ToolFunction(BaseModel):
    """Definição da função de uma ferramenta."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    """Ferramenta no formato interno do chat nativo."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["function"] = "function"
    function: ToolFunction


class ToolCallOut(BaseModel):
    """Chamada de ferramenta produzida pelo modelo."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """Corpo de `POST /chat` (sem `system_prompt` nem parâmetros de geração)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "messages": [{"role": "user", "content": "Explique SSE em uma frase."}],
                "stream": False,
                "model": "ollama/llama3.1",
                "tools": [],
                "tool_choice": "auto",
            }
        },
    )

    messages: list[ChatMessageInput] = Field(min_length=1)
    stream: bool = True
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description=(
            "Model ID `provider/model` ou alias. Omitido, usa `AI_DEFAULT_MODEL`."
        ),
    )
    tools: list[ToolDefinition] | None = Field(
        default=None,
        max_length=64,
        description="Ferramentas disponíveis; exige a capacidade `tools`.",
    )
    tool_choice: str | dict[str, Any] | None = Field(
        default=None,
        description="`auto`, `none`, `required` ou o nome de uma ferramenta.",
    )

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("não pode ser vazio")
        return value

    @field_validator("messages")
    @classmethod
    def _last_message_must_be_user(cls, messages: list[ChatMessageInput]) -> list[ChatMessageInput]:
        if messages and messages[-1].role != "user":
            raise ValueError("a última mensagem deve ter role = user")
        return messages


class AssistantMessage(BaseModel):
    """Mensagem de resposta do assistente."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str
    tool_calls: list[ToolCallOut] = Field(default_factory=list)


class ProviderUsage(BaseModel):
    """Tokens medidos pelo provedor, quando disponíveis."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ChatResponse(BaseModel):
    """Resposta completa do chat (`stream = false`)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "message": {
                    "role": "assistant",
                    "content": "SSE envia eventos incrementais.",
                    "tool_calls": [],
                },
                "provider": "ollama",
                "model": "llama3.1:latest",
                "model_id": "ollama/llama3.1",
                "finish_reason": "stop",
                "provider_usage": {"prompt_tokens": 42, "completion_tokens": 128},
                "usage": {
                    "daily_limit": 10,
                    "daily_remaining": 9,
                    "per_minute_limit": 3,
                    "per_minute_remaining": 2,
                    "resets_at": "2026-10-29T00:00:00Z",
                },
            }
        },
    )

    message: AssistantMessage
    provider: str
    model: str
    model_id: str
    finish_reason: FinishReason
    provider_usage: ProviderUsage
    usage: UsageInfo


class StreamChunk(BaseModel):
    """Pedaço de texto do streaming (`data: {"content": ...}`)."""

    model_config = ConfigDict(extra="forbid")

    content: str
    tool_calls: list[ToolCallOut] = Field(default_factory=list)


class StreamDone(BaseModel):
    """Evento final `done` (RF-CHAT-12/16)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["complete"] = "complete"
    finish_reason: FinishReason
    model_id: str | None = None
    provider_usage: ProviderUsage | None = None
    usage: UsageInfo


class StreamError(BaseModel):
    """Evento `error` emitido depois que o fluxo já começou (RF-CHAT-08)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    detail: str
    request_id: str


__all__ = [
    "AssistantMessage",
    "ChatMessageInput",
    "ChatRequest",
    "ChatResponse",
    "ProviderUsage",
    "StreamChunk",
    "StreamDone",
    "StreamError",
    "ToolCallOut",
    "ToolDefinition",
    "ToolFunction",
]

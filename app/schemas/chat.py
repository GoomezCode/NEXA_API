"""Schemas do chat (SRS §3.7.3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.base import FinishReason, MessageRole
from app.schemas.usage import UsageInfo


class ChatMessageInput(BaseModel):
    """Mensagem enviada pelo cliente."""

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str

    @field_validator("content")
    @classmethod
    def _content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("não pode ser vazio")
        return value


class ChatRequest(BaseModel):
    """Corpo de `POST /chat` (sem `system_prompt` nem parâmetros de geração)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "messages": [{"role": "user", "content": "Explique SSE em uma frase."}],
                "stream": False,
                "model": "llama3.1:latest",
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
            "Modelo instalado no Ollama para esta requisição. Omitido, usa o "
            "modelo padrão do servidor (`OLLAMA_MODEL`)."
        ),
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


class ChatResponse(BaseModel):
    """Resposta completa do chat (`stream = false`)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "message": {"role": "assistant", "content": "SSE envia eventos incrementais."},
                "provider": "ollama",
                "model": "llama3.1",
                "finish_reason": "stop",
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
    finish_reason: FinishReason
    usage: UsageInfo


class StreamChunk(BaseModel):
    """Pedaço de texto do streaming (`data: {"content": ...}`)."""

    model_config = ConfigDict(extra="forbid")

    content: str


class StreamDone(BaseModel):
    """Evento final `done` (RF-CHAT-12)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["complete"] = "complete"
    finish_reason: FinishReason
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
    "StreamChunk",
    "StreamDone",
    "StreamError",
]

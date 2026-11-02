"""Schemas do chat (SRS v2.0, §3.7.3)."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any, Literal
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.ai.base import FinishReason, ImageData, MessageRole
from app.schemas.usage import UsageInfo


class TextContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    text: str = Field(max_length=8000)


class ImageContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image"]
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    data: str = Field(min_length=1, max_length=28_000_000, description="Base64 sem prefixo data:.")

    @field_validator("data")
    @classmethod
    def _valid_base64(cls, value: str, info: ValidationInfo) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("imagem deve estar em base64 válido") from exc
        if len(decoded) > 20 * 1024 * 1024:
            raise ValueError("imagem pode ter no máximo 20 MB")
        media_type = info.data.get("media_type")
        signatures = {
            "image/png": decoded.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": decoded.startswith(b"\xff\xd8\xff"),
            "image/webp": len(decoded) >= 12
            and decoded[:4] == b"RIFF"
            and decoded[8:12] == b"WEBP",
            "image/gif": decoded.startswith((b"GIF87a", b"GIF89a")),
        }
        if not isinstance(media_type, str) or not signatures.get(media_type, False):
            raise ValueError("o conteúdo não corresponde ao media_type declarado")
        return value


ChatContentPart = Annotated[TextContentPart | ImageContentPart, Field(discriminator="type")]


class ChatMessageInput(BaseModel):
    """Mensagem enviada pelo cliente."""

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str | list[ChatContentPart]
    tool_call_id: str | None = Field(default=None, description="ID da chamada respondida.")

    @field_validator("content")
    @classmethod
    def _content_must_not_be_blank(
        cls, value: str | list[ChatContentPart]
    ) -> str | list[ChatContentPart]:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("não pode ser vazio")
            return value
        if not value or len(value) > 9:
            raise ValueError("a mensagem pode ter no máximo 9 partes")
        if not any(
            part.type == "image" or (part.type == "text" and part.text.strip()) for part in value
        ):
            raise ValueError("conteúdo deve incluir texto ou imagem")
        if sum(part.type == "image" for part in value) > 4:
            raise ValueError("no máximo 4 imagens por mensagem")
        return value

    @property
    def text_content(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "\n".join(part.text for part in self.content if part.type == "text")

    @property
    def images(self) -> tuple[ImageData, ...]:
        if isinstance(self.content, str):
            return ()
        return tuple(
            ImageData(media_type=part.media_type, data=part.data)
            for part in self.content
            if part.type == "image"
        )


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


class PersonalizationMemory(BaseModel):
    """Memória do usuário tratada como dado contextual."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=4000)


class PersonalizationInput(BaseModel):
    """Campos controlados pelo servidor para personalizar o contexto."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "display_name": "Marina",
                "memories": [{"title": "Preferências", "content": "Prefere respostas diretas."}],
            }
        },
    )

    display_name: str | None = Field(default=None, max_length=60)
    memories: list[PersonalizationMemory] = Field(default_factory=list, max_length=50)

    @field_validator("display_name")
    @classmethod
    def _safe_display_name(cls, value: str | None) -> str | None:
        if value is not None and any(category(char) == "Cc" for char in value):
            raise ValueError("nome não pode conter quebras de linha ou caracteres de controle")
        return value.strip() if value else value


class ChatRequest(BaseModel):
    """Corpo de `POST /chat` (sem system_prompt nem parâmetros de geração)."""

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
    personalization: PersonalizationInput | None = Field(default=None)
    stream: bool = True
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description=("Model ID `provider/model` ou alias. Omitido, usa `AI_DEFAULT_MODEL`."),
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
    key_source: Literal["server", "user"] = "server"
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
    key_source: Literal["server", "user"] = "server"
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

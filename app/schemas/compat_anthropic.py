"""Contrato da compatibilidade Anthropic (`/v1/messages`, RF-CMP-02/04/11)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: `stop_reason` do contrato Anthropic.
AnthropicStopReason = Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"]

#: Tipos de bloco aceitos na requisição (docs §4.4).
AnthropicRequestBlockType = Literal["text", "image", "tool_use", "tool_result"]


class AnthropicContentBlock(BaseModel):
    """Bloco de conteúdo de `messages[].content` ou `system`.

    O modelo é permissivo de propósito: tipos desconhecidos são rejeitados na
    tradução com `400 invalid_request_error`, mantendo a mensagem do contrato
    Anthropic em vez do 422 genérico do FastAPI.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    text: str | None = None
    source: dict[str, Any] | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None

    def is_supported(self) -> bool:
        return self.type in {"text", "image", "tool_use", "tool_result"}


class AnthropicMessage(BaseModel):
    """Mensagem de entrada (`user` ou `assistant`)."""

    model_config = ConfigDict(extra="allow")

    role: Literal["user", "assistant"]
    content: str | list[AnthropicContentBlock]

    @field_validator("content")
    @classmethod
    def _content_must_not_be_empty(
        cls, value: str | list[AnthropicContentBlock]
    ) -> str | list[AnthropicContentBlock]:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("content não pode ser vazio")
            return value
        if not value:
            raise ValueError("content exige ao menos um bloco")
        return value


class AnthropicTool(BaseModel):
    """Ferramenta declarada no contrato Anthropic (`input_schema`)."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class AnthropicToolChoice(BaseModel):
    """`tool_choice`: `auto`, `any`, `tool` ou `none`."""

    model_config = ConfigDict(extra="allow")

    type: Literal["auto", "any", "tool", "none"] = "auto"
    name: str | None = Field(default=None, max_length=128)
    disable_parallel_tool_use: bool | None = None


class _AnthropicConversation(BaseModel):
    """Campos comuns de `messages` e `count_tokens`."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1, max_length=256)
    messages: list[AnthropicMessage] = Field(min_length=1, max_length=100)
    system: str | list[AnthropicContentBlock] | None = None
    tools: list[AnthropicTool] | None = Field(default=None, max_length=64)
    tool_choice: AnthropicToolChoice | None = None

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model não pode ser vazio")
        return value


class AnthropicMessageRequest(_AnthropicConversation):
    """Corpo de `POST /v1/messages`."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "model": "anthropic/claude-sonnet-4-5",
                "max_tokens": 1024,
                "system": "Você é um assistente útil.",
                "messages": [{"role": "user", "content": "Olá!"}],
                "stream": False,
                "temperature": 0.7,
                "top_p": 1.0,
                "top_k": 40,
                "stop_sequences": ["\n\nHuman:"],
                "metadata": {"user_id": "usuario-123"},
            }
        },
    )

    max_tokens: int = Field(ge=1)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0, le=1)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1)
    stop_sequences: list[str] | None = Field(default=None, max_length=4)
    metadata: dict[str, Any] | None = None

    @field_validator("stop_sequences")
    @classmethod
    def _validate_stop_sequences(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not sequence for sequence in value):
            raise ValueError("stop_sequences não pode conter sequência vazia")
        return value


class AnthropicCountTokensRequest(_AnthropicConversation):
    """Corpo de `POST /v1/messages/count_tokens`."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "model": "anthropic/claude-sonnet-4-5",
                "system": "Você é um assistente útil.",
                "messages": [{"role": "user", "content": "Olá!"}],
            }
        },
    )


class AnthropicTextBlock(BaseModel):
    """Bloco `text` da resposta."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str


class AnthropicToolUseBlock(BaseModel):
    """Bloco `tool_use` da resposta (execução é do cliente)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


AnthropicOutputBlock = Annotated[
    AnthropicTextBlock | AnthropicToolUseBlock,
    Field(discriminator="type"),
]


class AnthropicUsage(BaseModel):
    """Tokens de entrada/saída, no shape Anthropic."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int
    output_tokens: int


class AnthropicMessageResponse(BaseModel):
    """Resposta de `POST /v1/messages` (CA-34)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "id": "msg_nexa_0192f3a4000070008000",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": "Olá! Como posso ajudar?"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 8},
            }
        },
    )

    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    model: str
    content: list[AnthropicOutputBlock]
    stop_reason: AnthropicStopReason
    stop_sequence: str | None = None
    usage: AnthropicUsage


class AnthropicCountTokensResponse(BaseModel):
    """Resposta de `POST /v1/messages/count_tokens`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"input_tokens": 42}},
    )

    input_tokens: int


__all__ = [
    "AnthropicContentBlock",
    "AnthropicCountTokensRequest",
    "AnthropicCountTokensResponse",
    "AnthropicMessage",
    "AnthropicMessageRequest",
    "AnthropicMessageResponse",
    "AnthropicOutputBlock",
    "AnthropicStopReason",
    "AnthropicTextBlock",
    "AnthropicTool",
    "AnthropicToolChoice",
    "AnthropicToolUseBlock",
    "AnthropicUsage",
]

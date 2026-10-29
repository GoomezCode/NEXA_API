"""Contrato da compatibilidade OpenAI (`/v1`, RF-CMP-01/04/07/08)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: `finish_reason` do contrato OpenAI.
OpenAIFinishReason = Literal["stop", "length", "tool_calls", "content_filter"]


class OpenAIMessage(BaseModel):
    """Mensagem no contrato OpenAI (`system`, `user`, `assistant`, `tool`)."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[OpenAIToolCall] | None = None

    @model_validator(mode="after")
    def _check_required_fields(self) -> OpenAIMessage:
        if self.role in {"system", "user"} and (self.content is None or not self.content.strip()):
            raise ValueError("content é obrigatório para mensagens system/user")
        if self.role == "tool":
            if self.content is None:
                raise ValueError("content é obrigatório para mensagens tool")
            if not self.tool_call_id:
                raise ValueError("tool_call_id é obrigatório para mensagens tool")
        if self.role == "assistant" and self.content is None and not self.tool_calls:
            raise ValueError("mensagens assistant exigem content ou tool_calls")
        return self


class OpenAIToolCallFunction(BaseModel):
    """Função chamada pelo modelo (argumentos em string JSON, como na OpenAI)."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=128)
    arguments: str = "{}"


class OpenAIToolCall(BaseModel):
    """Chamada de ferramenta no formato OpenAI."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: OpenAIToolCallFunction


class OpenAIToolFunction(BaseModel):
    """Definição da função de uma ferramenta."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    parameters: dict[str, Any] = Field(default_factory=dict)


class OpenAITool(BaseModel):
    """Ferramenta declarada no contrato OpenAI."""

    model_config = ConfigDict(extra="allow")

    type: Literal["function"] = "function"
    function: OpenAIToolFunction


class OpenAIResponseFormat(BaseModel):
    """`response_format`; `json_object`/`json_schema` exigem `json_mode`."""

    model_config = ConfigDict(extra="allow")

    type: Literal["text", "json_object", "json_schema"] = "text"


class OpenAIStreamOptions(BaseModel):
    """`stream_options`; `include_usage` envia o chunk final de uso."""

    model_config = ConfigDict(extra="allow")

    include_usage: bool = False


class OpenAIChatRequest(BaseModel):
    """Corpo de `POST /v1/chat/completions`."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "Seja breve."},
                    {"role": "user", "content": "Olá!"},
                ],
                "stream": False,
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 256,
                "stop": ["\n\n"],
                "response_format": {"type": "text"},
            }
        },
    )

    model: str = Field(min_length=1, max_length=256)
    messages: list[OpenAIMessage] = Field(min_length=1)
    stream: bool = False
    stream_options: OpenAIStreamOptions | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    tools: list[OpenAITool] | None = Field(default=None, max_length=64)
    tool_choice: str | dict[str, Any] | None = None
    response_format: OpenAIResponseFormat | None = None
    user: str | None = Field(default=None, max_length=256)
    n: int = Field(default=1, ge=1)
    logprobs: bool | None = None
    top_logprobs: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    logit_bias: dict[str, float] | None = None
    parallel_tool_calls: bool | None = None

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model não pode ser vazio")
        return value

    @field_validator("stop")
    @classmethod
    def _validate_stop(cls, value: str | list[str] | None) -> str | list[str] | None:
        if value is None:
            return None
        sequences = [value] if isinstance(value, str) else value
        if len(sequences) > 4:
            raise ValueError("no máximo 4 sequências de parada")
        if any(not sequence for sequence in sequences):
            raise ValueError("sequências de parada não podem ser vazias")
        return sequences

    def stop_sequences(self) -> list[str]:
        if self.stop is None:
            return []
        return [self.stop] if isinstance(self.stop, str) else list(self.stop)

    def requested_max_tokens(self, default: int) -> int:
        """`max_tokens`/`max_completion_tokens`, limitado pelo servidor."""
        requested = self.max_tokens or self.max_completion_tokens or default
        return min(requested, default)

    def wants_json(self) -> bool:
        return self.response_format is not None and self.response_format.type in {
            "json_object",
            "json_schema",
        }


class OpenAIToolCallOut(BaseModel):
    """Chamada de ferramenta na resposta."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["function"] = "function"
    function: OpenAIToolCallFunction


class OpenAIAssistantMessage(BaseModel):
    """Mensagem `assistant` devolvida ao cliente."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[OpenAIToolCallOut] = Field(default_factory=list)


class OpenAIChoice(BaseModel):
    """Escolha da resposta (`n = 1`)."""

    model_config = ConfigDict(extra="forbid")

    index: int = 0
    message: OpenAIAssistantMessage
    finish_reason: OpenAIFinishReason


class OpenAIUsage(BaseModel):
    """Tokens da resposta, no shape OpenAI."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OpenAIChatResponse(BaseModel):
    """Resposta de `POST /v1/chat/completions` (CA-31)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "id": "chatcmpl-nexa-0192f3a4000070008000",
                "object": "chat.completion",
                "created": 1761748200,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Olá! Como posso ajudar?",
                            "tool_calls": [],
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }
        },
    )

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChoice]
    usage: OpenAIUsage


class OpenAIModel(BaseModel):
    """Modelo no shape OpenAI."""

    model_config = ConfigDict(extra="forbid")

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "nexa"


class OpenAIModelList(BaseModel):
    """Resposta de `GET /v1/models` (CA-33)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "object": "list",
                "data": [
                    {
                        "id": "openai/gpt-4o-mini",
                        "object": "model",
                        "created": 1761748200,
                        "owned_by": "nexa",
                    }
                ],
            }
        },
    )

    object: Literal["list"] = "list"
    data: list[OpenAIModel]


def dump_arguments(arguments: dict[str, Any]) -> str:
    """Argumentos de ferramenta como string JSON (contrato OpenAI)."""
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))


# `OpenAIMessage` referencia `OpenAIToolCall`, definido depois por legibilidade.
OpenAIMessage.model_rebuild()


__all__ = [
    "OpenAIAssistantMessage",
    "OpenAIChatRequest",
    "OpenAIChatResponse",
    "OpenAIChoice",
    "OpenAIFinishReason",
    "OpenAIMessage",
    "OpenAIModel",
    "OpenAIModelList",
    "OpenAIResponseFormat",
    "OpenAIStreamOptions",
    "OpenAITool",
    "OpenAIToolCall",
    "OpenAIToolCallFunction",
    "OpenAIToolCallOut",
    "OpenAIToolFunction",
    "OpenAIUsage",
    "dump_arguments",
]

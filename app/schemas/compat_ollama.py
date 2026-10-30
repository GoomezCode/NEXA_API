"""Contrato da compatibilidade Ollama (`/api`, RF-CMP-03/04/08)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: `done_reason` do contrato Ollama.
OllamaDoneReason = Literal["stop", "length", "tool_calls"]


class OllamaFunction(BaseModel):
    """Definição da função de uma ferramenta."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    parameters: dict[str, Any] = Field(default_factory=dict)


class OllamaTool(BaseModel):
    """Ferramenta declarada no contrato Ollama."""

    model_config = ConfigDict(extra="allow")

    type: Literal["function"] = "function"
    function: OllamaFunction


class OllamaToolCallFunction(BaseModel):
    """Chamada de ferramenta (argumentos como objeto JSON)."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments", mode="before")
    @classmethod
    def _parse_arguments(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                parsed = json.loads(value or "{}")
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value


class OllamaToolCall(BaseModel):
    """Chamada de ferramenta em `messages[].tool_calls`."""

    model_config = ConfigDict(extra="allow")

    function: OllamaToolCallFunction


class OllamaToolCallOut(BaseModel):
    """Chamada de ferramenta devolvida ao cliente Ollama."""

    model_config = ConfigDict(extra="forbid")

    function: OllamaToolCallFunction


class OllamaMessage(BaseModel):
    """Mensagem no contrato Ollama."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    images: list[str] | None = None
    tool_calls: list[OllamaToolCall] | None = None
    tool_name: str | None = None


class OllamaChatRequest(BaseModel):
    """Corpo de `POST /api/chat`."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "model": "ollama/llama3.1",
                "messages": [{"role": "user", "content": "Olá!"}],
                "stream": False,
                "tools": [],
                "options": {"temperature": 0.7, "num_predict": 256},
                "format": "json",
                "keep_alive": "5m",
            }
        },
    )

    model: str = Field(min_length=1, max_length=256)
    messages: list[OllamaMessage] = Field(min_length=1, max_length=100)
    stream: bool = True
    tools: list[OllamaTool] | None = Field(default=None, max_length=64)
    options: dict[str, Any] = Field(default_factory=dict)
    format: str | dict[str, Any] | None = None
    keep_alive: str | int | None = None

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model não pode ser vazio")
        return value

    def requested_max_tokens(self, default: int) -> int:
        """`options.num_predict`, limitado pelo servidor; valores <= 0 usam o padrão."""
        raw = self.options.get("num_predict")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return min(raw, default)
        return default

    def wants_json(self) -> bool:
        return self.format is not None

    def stop_sequences(self) -> list[str]:
        raw = self.options.get("stop")
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, str) and item]
        return []


class OllamaChatMessage(BaseModel):
    """Mensagem `assistant` da resposta."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[OllamaToolCallOut] = Field(default_factory=list)


class OllamaChatResponse(BaseModel):
    """Resposta não-streaming de `POST /api/chat` (CA-37)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "model": "llama3.1:latest",
                "created_at": "2026-10-29T14:30:00Z",
                "message": {"role": "assistant", "content": "Olá! Como posso ajudar?"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 12,
                "eval_count": 8,
            }
        },
    )

    model: str
    created_at: str
    message: OllamaChatMessage
    done: Literal[True] = True
    done_reason: OllamaDoneReason
    prompt_eval_count: int
    eval_count: int


class OllamaModelDetails(BaseModel):
    """Detalhes de um modelo no shape Ollama."""

    model_config = ConfigDict(extra="forbid")

    family: str = ""
    parameter_size: str = ""
    quantization_level: str = ""


class OllamaTagModel(BaseModel):
    """Modelo listado por `GET /api/tags`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    modified_at: str
    size: int
    digest: str
    details: OllamaModelDetails


class OllamaTagsResponse(BaseModel):
    """Resposta de `GET /api/tags` (CA-37)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "models": [
                    {
                        "name": "ollama/llama3.1",
                        "model": "ollama/llama3.1",
                        "modified_at": "2026-10-29T14:30:00Z",
                        "size": 4920739055,
                        "digest": "sha256:0192f3a4",
                        "details": {
                            "family": "llama",
                            "parameter_size": "8.0B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                ]
            }
        },
    )

    models: list[OllamaTagModel]


class OllamaShowRequest(BaseModel):
    """Corpo de `POST /api/show`."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={"example": {"model": "ollama/llama3.1"}},
    )

    model: str = Field(min_length=1, max_length=256)
    verbose: bool = False

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model não pode ser vazio")
        return value


class OllamaShowResponse(BaseModel):
    """Resposta de `POST /api/show`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "model": "ollama/llama3.1",
                "details": {
                    "family": "llama",
                    "parameter_size": "8.0B",
                    "quantization_level": "Q4_K_M",
                },
                "capabilities": ["completion", "tools"],
                "modified_at": "2026-10-29T14:30:00Z",
            }
        },
    )

    model: str
    details: OllamaModelDetails
    capabilities: list[str]
    modified_at: str


class OllamaPsModel(BaseModel):
    """Modelo em execução (o gateway não mantém estado)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    size: int
    size_vram: int
    expires_at: str | None = None


class OllamaPsResponse(BaseModel):
    """Resposta de `GET /api/ps`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"models": []}},
    )

    models: list[OllamaPsModel]


__all__ = [
    "OllamaChatMessage",
    "OllamaChatRequest",
    "OllamaChatResponse",
    "OllamaDoneReason",
    "OllamaFunction",
    "OllamaMessage",
    "OllamaModelDetails",
    "OllamaPsModel",
    "OllamaPsResponse",
    "OllamaShowRequest",
    "OllamaShowResponse",
    "OllamaTagModel",
    "OllamaTagsResponse",
    "OllamaTool",
    "OllamaToolCall",
    "OllamaToolCallFunction",
    "OllamaToolCallOut",
]

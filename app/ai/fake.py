"""Provedor determinístico para testes (CA-15).

Permite simular respostas, falhas em diferentes pontos do stream, lentidão e
indisponibilidade no health check, sem qualquer acesso à rede.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from app.ai.base import (
    ChatMessage,
    FinishReason,
    GenerationChunk,
    GenerationResult,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """Chamada recebida pelo provider fake."""

    messages: tuple[ChatMessage, ...]
    max_tokens: int
    max_seconds: float
    request_id: str | None
    streamed: bool


def _coerce_error(error: str | BaseException) -> BaseException:
    """Aceita atalhos textuais ou uma exceção pronta."""
    if isinstance(error, BaseException):
        return error
    mapping: dict[str, BaseException] = {
        "timeout": ProviderTimeout("timeout simulado"),
        "unavailable": ProviderUnavailable("provedor indisponível (simulado)"),
        "error": ProviderError("erro simulado"),
    }
    return mapping.get(error, ProviderError(str(error)))


@dataclass
class FakeProvider:
    """`AIProvider` controlável, usado em testes e em CA-15."""

    name: str = field(default="fake", init=False)
    model: str = "fake-model"
    chunks: tuple[str, ...] = ("Olá", " do fake!")
    finish_reason: FinishReason = "stop"
    prompt_tokens: int | None = 11
    completion_tokens: int | None = 7
    healthy: bool = True
    error: str | BaseException | None = None
    error_after_chunks: int = 0
    delay: float = 0.0
    health_error: BaseException | None = None
    calls: list[RecordedCall] = field(default_factory=list, init=False)

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
    ) -> GenerationResult:
        self._record(messages, max_tokens, max_seconds, request_id, streamed=False)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise _coerce_error(self.error)
        return GenerationResult(
            content="".join(self.chunks),
            finish_reason=self.finish_reason,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
    ) -> AsyncIterator[GenerationChunk]:
        self._record(messages, max_tokens, max_seconds, request_id, streamed=True)
        for index, content in enumerate(self.chunks):
            if self.error is not None and index >= self.error_after_chunks:
                raise _coerce_error(self.error)
            if self.delay:
                await asyncio.sleep(self.delay)
            yield GenerationChunk(content=content)

        if self.error is not None and len(self.chunks) <= self.error_after_chunks:
            raise _coerce_error(self.error)

        yield GenerationChunk(
            finish_reason=self.finish_reason,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )

    async def health(self) -> bool:
        if self.health_error is not None:
            raise self.health_error
        return self.healthy

    def _record(
        self,
        messages: Sequence[ChatMessage],
        max_tokens: int,
        max_seconds: float,
        request_id: str | None,
        *,
        streamed: bool,
    ) -> None:
        self.calls.append(
            RecordedCall(
                messages=tuple(messages),
                max_tokens=max_tokens,
                max_seconds=max_seconds,
                request_id=request_id,
                streamed=streamed,
            )
        )


__all__ = ["FakeProvider", "RecordedCall"]

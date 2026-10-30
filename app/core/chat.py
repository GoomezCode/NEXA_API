"""Regras de contexto do chat independentes de rota (RF-CHAT-04/09/10, RN-06)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.ai.base import MessageRole
from app.config import Settings
from app.core.errors import ValidationErrorItem
from app.core.security import Role


class ContextMessage(Protocol):
    """Mensagem mínima aceita pelas regras de contexto (nativa ou compatível)."""

    @property
    def role(self) -> MessageRole: ...

    @property
    def content(self) -> str: ...


def context_errors(
    messages: Sequence[ContextMessage],
    *,
    role: Role,
    settings: Settings,
    allow_system: bool = False,
) -> list[ValidationErrorItem]:
    """Valida limites e mensagens `system` do papel do chamador.

    `allow_system` é usado pelas superfícies de compatibilidade, onde o campo
    `system` faz parte do protocolo externo (ex.: Anthropic/Claude Code).
    """
    errors: list[ValidationErrorItem] = []
    max_messages, max_chars = limits_for(role=role, settings=settings)

    if len(messages) > max_messages:
        errors.append(
            ValidationErrorItem(
                field="messages",
                reason=f"no máximo {max_messages} mensagens por requisição",
            )
        )

    for index, message in enumerate(messages):
        if len(message.content) > max_chars:
            errors.append(
                ValidationErrorItem(
                    field=f"messages.{index}.content",
                    reason=f"no máximo {max_chars} caracteres por mensagem",
                )
            )

    first_user = next(
        (index for index, message in enumerate(messages) if message.role == "user"),
        None,
    )
    for index, message in enumerate(messages):
        if message.role != "system" or allow_system:
            continue
        if role == "guest":
            errors.append(
                ValidationErrorItem(
                    field=f"messages.{index}.role",
                    reason="mensagens system não são permitidas para convidados",
                )
            )
        elif not settings.allow_client_system_messages:
            errors.append(
                ValidationErrorItem(
                    field=f"messages.{index}.role",
                    reason="mensagens system do cliente não são permitidas",
                )
            )
        elif first_user is not None and index > first_user:
            errors.append(
                ValidationErrorItem(
                    field=f"messages.{index}.role",
                    reason="mensagens system devem vir antes da primeira mensagem user",
                )
            )

    return errors


def limits_for(*, role: Role, settings: Settings) -> tuple[int, int]:
    """`(máx. mensagens, máx. caracteres)` do papel."""
    if role == "admin":
        return settings.max_messages_per_request, settings.max_message_chars
    return settings.guest_max_messages_per_request, settings.guest_max_message_chars


__all__ = ["ContextMessage", "context_errors", "limits_for"]

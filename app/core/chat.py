"""Regras de contexto do chat independentes de rota (RF-CHAT-04/09/10, RN-06)."""

from __future__ import annotations

from collections.abc import Sequence

from app.config import Settings
from app.core.errors import ValidationErrorItem
from app.core.security import Role
from app.schemas.chat import ChatMessageInput


def context_errors(
    messages: Sequence[ChatMessageInput],
    *,
    role: Role,
    settings: Settings,
) -> list[ValidationErrorItem]:
    """Valida limites e mensagens `system` do papel do chamador."""
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
        if message.role != "system":
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


__all__ = ["context_errors", "limits_for"]

"""Cálculos de cota reutilizados pela autenticação e, adiante, pelo chat.

A Fase 3 substitui `initial_usage` por um snapshot do `UsageStore`; os helpers
de calendário permanecem.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from app.config import Settings
from app.schemas.usage import UsageInfo


def daily_reset_at(now: datetime) -> datetime:
    """Próxima 00:00 UTC (janela diária fixa, RN-09)."""
    current = now.astimezone(UTC)
    return datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=UTC)


def initial_usage(settings: Settings, *, now: datetime) -> UsageInfo:
    """`usage` de um `gid` recém-criado (sem consumo).

    A cota efetiva é o menor valor entre a do `gid` e a do IP (RF-USO-12).
    """
    remaining = min(settings.guest_daily_messages, settings.guest_ip_daily_messages)
    return UsageInfo(
        daily_limit=settings.guest_daily_messages,
        daily_remaining=remaining,
        per_minute_limit=settings.guest_rate_per_min,
        per_minute_remaining=settings.guest_rate_per_min,
        resets_at=daily_reset_at(now),
    )


__all__ = ["daily_reset_at", "initial_usage"]

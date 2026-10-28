"""Cálculos e cabeçalhos de cota compartilhados pelas rotas (RD-08, RF-USO-08)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, time, timedelta

from app.schemas.usage import UsageInfo


def daily_reset_at(now: datetime) -> datetime:
    """Próxima 00:00 UTC (janela diária fixa, RN-09)."""
    current = now.astimezone(UTC)
    return datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=UTC)


def seconds_until(when: datetime, *, now: datetime) -> int:
    """Segundos (mínimo 1) até `when`, para `Retry-After`."""
    return max(1, math.ceil((when - now).total_seconds()))


def rate_limit_headers(usage: UsageInfo) -> dict[str, str]:
    """Cabeçalhos `X-RateLimit-*` da cota diária (omitidos para o admin)."""
    if usage.daily_limit is None or usage.daily_remaining is None or usage.resets_at is None:
        return {}
    return {
        "X-RateLimit-Limit": str(usage.daily_limit),
        "X-RateLimit-Remaining": str(usage.daily_remaining),
        "X-RateLimit-Reset": str(int(usage.resets_at.timestamp())),
    }


__all__ = ["daily_reset_at", "rate_limit_headers", "seconds_until"]

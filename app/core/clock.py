"""Relógio injetável (facilita testes de janelas de cota)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Fonte de tempo em UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """Relógio real, sempre em UTC."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)

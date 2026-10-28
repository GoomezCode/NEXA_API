"""Testes do relógio injetável (F0-03)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.clock import SystemClock


def test_system_clock_returns_utc_now() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC
    assert abs((datetime.now(UTC) - now).total_seconds()) < 5

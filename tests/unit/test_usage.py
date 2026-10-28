"""Testes dos helpers de cota e do schema `usage` (RD-08)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.core.usage import daily_reset_at, initial_usage
from app.schemas.usage import UsageInfo, UsageResponse
from tests.conftest import make_settings


def test_daily_reset_is_next_midnight_utc() -> None:
    now = datetime(2026, 10, 28, 23, 59, 59, tzinfo=UTC)
    assert daily_reset_at(now) == datetime(2026, 10, 29, 0, 0, tzinfo=UTC)


def test_daily_reset_converts_to_utc() -> None:
    local = datetime(2026, 10, 28, 10, 0, tzinfo=timezone(timedelta(hours=-3)))
    assert daily_reset_at(local) == datetime(2026, 10, 29, 0, 0, tzinfo=UTC)


def test_initial_usage_uses_effective_quota() -> None:
    settings = make_settings(
        guest_daily_messages=10,
        guest_ip_daily_messages=30,
        guest_rate_per_min=3,
    )
    usage = initial_usage(settings, now=datetime(2026, 10, 28, 12, 0, tzinfo=UTC))

    assert usage.daily_limit == 10
    assert usage.daily_remaining == 10
    assert usage.per_minute_limit == 3
    assert usage.per_minute_remaining == 3
    assert usage.model_dump(mode="json")["resets_at"] == "2026-10-29T00:00:00Z"


def test_initial_usage_respects_lower_ip_quota() -> None:
    settings = make_settings(guest_daily_messages=10, guest_ip_daily_messages=2)
    usage = initial_usage(settings, now=datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    assert usage.daily_limit == 10
    assert usage.daily_remaining == 2


def test_admin_usage_has_null_daily_fields() -> None:
    response = UsageResponse(
        role="admin",
        daily_limit=None,
        daily_remaining=None,
        per_minute_limit=60,
        per_minute_remaining=59,
        resets_at=None,
    )
    assert response.model_dump(mode="json") == {
        "role": "admin",
        "daily_limit": None,
        "daily_remaining": None,
        "per_minute_limit": 60,
        "per_minute_remaining": 59,
        "resets_at": None,
    }


def test_usage_serializes_none_reset() -> None:
    usage = UsageInfo(
        daily_limit=None,
        daily_remaining=None,
        per_minute_limit=1,
        per_minute_remaining=1,
    )
    assert usage.model_dump(mode="json")["resets_at"] is None

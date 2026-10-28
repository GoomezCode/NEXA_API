"""Testes dos helpers de cota e do schema `usage` (RD-08)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.core.usage import daily_reset_at, rate_limit_headers, seconds_until
from app.schemas.usage import UsageInfo, UsageResponse


def test_daily_reset_is_next_midnight_utc() -> None:
    now = datetime(2026, 10, 28, 23, 59, 59, tzinfo=UTC)
    assert daily_reset_at(now) == datetime(2026, 10, 29, 0, 0, tzinfo=UTC)


def test_daily_reset_converts_to_utc() -> None:
    local = datetime(2026, 10, 28, 10, 0, tzinfo=timezone(timedelta(hours=-3)))
    assert daily_reset_at(local) == datetime(2026, 10, 29, 0, 0, tzinfo=UTC)


def test_seconds_until_rounds_up_and_has_minimum_one() -> None:
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    assert seconds_until(now + timedelta(seconds=2.2), now=now) == 3
    assert seconds_until(now - timedelta(seconds=5), now=now) == 1


def test_rate_limit_headers_from_usage() -> None:
    usage = UsageInfo(
        daily_limit=10,
        daily_remaining=7,
        per_minute_limit=3,
        per_minute_remaining=2,
        resets_at=datetime(2026, 10, 29, 0, 0, tzinfo=UTC),
    )

    headers = rate_limit_headers(usage)

    assert headers == {
        "X-RateLimit-Limit": "10",
        "X-RateLimit-Remaining": "7",
        "X-RateLimit-Reset": str(int(datetime(2026, 10, 29, 0, 0, tzinfo=UTC).timestamp())),
    }


def test_rate_limit_headers_are_omitted_for_admin() -> None:
    usage = UsageInfo(
        daily_limit=None,
        daily_remaining=None,
        per_minute_limit=60,
        per_minute_remaining=59,
        resets_at=None,
    )
    assert rate_limit_headers(usage) == {}


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

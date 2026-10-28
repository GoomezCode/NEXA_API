"""Testes das estatísticas em memória (RF-ADM-02)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.stats import UsageStats
from tests.conftest import FakeClock


def make_stats(
    clock: FakeClock | None = None,
    *,
    retention_seconds: float = 3600.0,
    max_guests: int = 10_000,
) -> tuple[UsageStats, FakeClock]:
    resolved = clock or FakeClock()
    stats = UsageStats(resolved, retention_seconds=retention_seconds, max_guests=max_guests)
    return stats, resolved


def test_initial_snapshot_is_empty() -> None:
    stats, clock = make_stats()

    snapshot = stats.snapshot()

    assert snapshot.since == clock.now()
    assert snapshot.chat_requests_total == 0
    assert snapshot.guest_distinct == 0
    assert snapshot.rate_limited_total == 0
    assert snapshot.provider_errors_total == 0
    assert snapshot.avg_response_seconds == 0.0


def test_chat_counters_include_admin_but_distinct_only_guests() -> None:
    stats, _ = make_stats()

    stats.record_chat(subject="admin", role="admin")
    stats.record_chat(subject="gid-1", role="guest")
    stats.record_chat(subject="gid-1", role="guest")
    stats.record_chat(subject="gid-2", role="guest")

    snapshot = stats.snapshot()

    assert snapshot.chat_requests_total == 4
    assert snapshot.guest_distinct == 2


def test_distinct_guests_expire_after_the_retention_window() -> None:
    stats, clock = make_stats(retention_seconds=60)

    stats.record_chat(subject="gid-1", role="guest")
    clock.advance(30)
    stats.record_chat(subject="gid-2", role="guest")
    assert stats.snapshot().guest_distinct == 2

    clock.advance(31)

    assert stats.snapshot().guest_distinct == 1


def test_distinct_guests_are_bounded_by_max_guests() -> None:
    stats, _ = make_stats(max_guests=2)

    stats.record_chat(subject="gid-1", role="guest")
    stats.record_chat(subject="gid-2", role="guest")
    stats.record_chat(subject="gid-3", role="guest")

    assert stats.snapshot().guest_distinct == 2

    # Reativar o guest mais antigo o torna o mais recente e descarta outro.
    stats.record_chat(subject="gid-1", role="guest")
    stats.record_chat(subject="gid-4", role="guest")

    assert stats.snapshot().guest_distinct == 2


def test_rate_limited_and_provider_errors_are_counted() -> None:
    stats, _ = make_stats()

    stats.record_rate_limited()
    stats.record_rate_limited()
    stats.record_provider_error()

    snapshot = stats.snapshot()

    assert snapshot.rate_limited_total == 2
    assert snapshot.provider_errors_total == 1


def test_average_duration_is_rounded() -> None:
    stats, _ = make_stats()

    stats.record_duration(1.0)
    stats.record_duration(2.0)
    stats.record_duration(2.5)

    assert stats.snapshot().avg_response_seconds == 1.83


def test_since_uses_utc() -> None:
    clock = FakeClock(datetime(2026, 10, 28, 9, 0, tzinfo=UTC))
    stats, _ = make_stats(clock)

    assert stats.snapshot().since == datetime(2026, 10, 28, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "kwargs",
    [{"retention_seconds": 0}, {"retention_seconds": -1}, {"max_guests": 0}],
)
def test_invalid_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        UsageStats(FakeClock(), **kwargs)  # type: ignore[arg-type]


def test_negative_duration_is_rejected() -> None:
    stats, _ = make_stats()

    with pytest.raises(ValueError):
        stats.record_duration(-0.1)

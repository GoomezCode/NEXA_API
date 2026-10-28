"""Testes do UsageStore em memória (F3-01 a F3-05)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.core.usage_store import InMemoryUsageStore
from tests.conftest import FakeClock, make_settings

GUEST = "gid-1"
IP = "203.0.113.10"


def make_store(
    clock: FakeClock | None = None,
    *,
    purge_interval_seconds: float = 300.0,
    **overrides: object,
) -> tuple[InMemoryUsageStore, FakeClock]:
    resolved = clock or FakeClock()
    store = InMemoryUsageStore(
        make_settings(**overrides), resolved, purge_interval_seconds=purge_interval_seconds
    )
    return store, resolved


async def test_snapshot_initial_guest_usage() -> None:
    store, _ = make_store()

    usage = await store.snapshot(role="guest", subject=GUEST, ip=IP)

    assert usage.daily_limit == 10
    assert usage.daily_remaining == 10
    assert usage.per_minute_limit == 3
    assert usage.per_minute_remaining == 3
    assert usage.resets_at == datetime(2026, 10, 29, 0, 0, tzinfo=UTC)


async def test_snapshot_uses_lower_ip_quota() -> None:
    store, _ = make_store(guest_daily_messages=10, guest_ip_daily_messages=4)

    usage = await store.snapshot(role="guest", subject=GUEST, ip=IP)

    assert usage.daily_limit == 10
    assert usage.daily_remaining == 4


async def test_consume_decrements_gid_and_ip() -> None:
    store, _ = make_store(guest_daily_messages=10, guest_ip_daily_messages=4)

    first = await store.consume(role="guest", subject=GUEST, ip=IP)
    second = await store.consume(role="guest", subject=GUEST, ip=IP)

    assert first.allowed and second.allowed
    assert first.usage.daily_remaining == 3
    assert second.usage.daily_remaining == 2
    assert second.usage.per_minute_remaining == 1
    assert first.code is None
    assert second.retry_after == 0


async def test_ip_quota_is_shared_between_guests() -> None:
    store, _ = make_store(guest_daily_messages=10, guest_ip_daily_messages=2)

    assert (await store.consume(role="guest", subject="a", ip=IP)).allowed
    assert (await store.consume(role="guest", subject="b", ip=IP)).allowed

    blocked = await store.consume(role="guest", subject="c", ip=IP)
    assert blocked.allowed is False
    assert blocked.code == "ip_daily_limit_reached"
    assert blocked.usage.daily_remaining == 0

    # Outro IP continua com a cota do gid intacta (10) e a do IP cheia (2).
    other = await store.snapshot(role="guest", subject="c", ip="198.51.100.7")
    assert other.daily_remaining == 2
    assert (await store.consume(role="guest", subject="c", ip="198.51.100.7")).allowed


async def test_gid_daily_limit_is_enforced_without_consuming() -> None:
    store, _ = make_store(guest_daily_messages=2, guest_ip_daily_messages=3, guest_rate_per_min=10)

    await store.consume(role="guest", subject=GUEST, ip=IP)
    await store.consume(role="guest", subject=GUEST, ip=IP)

    blocked = await store.consume(role="guest", subject=GUEST, ip=IP)

    assert blocked.allowed is False
    assert blocked.code == "guest_daily_limit_reached"
    assert blocked.usage.daily_remaining == 0
    assert blocked.retry_after == 12 * 3600

    # A cota do IP não foi consumida pela tentativa barrada: restam 1 unidade
    # de IP (3 - 2) para o próximo gid.
    other = await store.snapshot(role="guest", subject="outro", ip=IP)
    assert other.daily_remaining == 1


async def test_both_quotas_exhausted_prioritizes_gid_code() -> None:
    store, _ = make_store(guest_daily_messages=1, guest_ip_daily_messages=1)

    assert (await store.consume(role="guest", subject=GUEST, ip=IP)).allowed
    blocked = await store.consume(role="guest", subject=GUEST, ip=IP)

    assert blocked.code == "guest_daily_limit_reached"


async def test_per_minute_limit_is_sliding() -> None:
    store, clock = make_store(
        guest_daily_messages=10, guest_ip_daily_messages=30, guest_rate_per_min=2
    )

    assert (await store.consume(role="guest", subject=GUEST, ip=IP)).allowed
    assert (await store.consume(role="guest", subject=GUEST, ip=IP)).allowed

    blocked = await store.consume(role="guest", subject=GUEST, ip=IP)
    assert blocked.allowed is False
    assert blocked.code == "rate_limited"
    assert blocked.retry_after == 60
    assert blocked.usage.daily_remaining == 8
    assert blocked.usage.per_minute_remaining == 0

    clock.advance(30)
    still_blocked = await store.consume(role="guest", subject=GUEST, ip=IP)
    assert still_blocked.allowed is False
    assert still_blocked.retry_after == 30

    clock.advance(31)
    assert (await store.consume(role="guest", subject=GUEST, ip=IP)).allowed


async def test_admin_has_no_daily_quota_but_has_minute_limit() -> None:
    store, clock = make_store(rate_limit_admin_chat_per_min=2)

    snapshot = await store.snapshot(role="admin", subject="admin", ip=IP)
    assert snapshot.daily_limit is None
    assert snapshot.daily_remaining is None
    assert snapshot.resets_at is None
    assert snapshot.per_minute_limit == 2
    assert snapshot.per_minute_remaining == 2

    assert (await store.consume(role="admin", subject="admin", ip=IP)).allowed
    assert (await store.consume(role="admin", subject="admin", ip=IP)).allowed

    blocked = await store.consume(role="admin", subject="admin", ip=IP)
    assert blocked.allowed is False
    assert blocked.code == "rate_limited"
    assert blocked.retry_after == 60
    assert blocked.usage.daily_limit is None

    clock.advance(60)
    assert (await store.consume(role="admin", subject="admin", ip=IP)).allowed


async def test_admin_limits_are_per_subject() -> None:
    store, _ = make_store(rate_limit_admin_chat_per_min=1)

    assert (await store.consume(role="admin", subject="a", ip=IP)).allowed
    assert (await store.consume(role="admin", subject="b", ip=IP)).allowed
    assert (await store.consume(role="admin", subject="a", ip=IP)).allowed is False


async def test_refund_restores_gid_and_ip() -> None:
    store, _ = make_store(guest_daily_messages=10, guest_ip_daily_messages=3)

    await store.consume(role="guest", subject=GUEST, ip=IP)
    await store.consume(role="guest", subject=GUEST, ip=IP)
    assert (await store.snapshot(role="guest", subject=GUEST, ip=IP)).daily_remaining == 1

    await store.refund(role="guest", subject=GUEST, ip=IP)
    assert (await store.snapshot(role="guest", subject=GUEST, ip=IP)).daily_remaining == 2

    await store.refund(role="guest", subject=GUEST, ip=IP)
    assert (await store.snapshot(role="guest", subject=GUEST, ip=IP)).daily_remaining == 3


async def test_refund_never_goes_negative() -> None:
    store, _ = make_store()

    await store.consume(role="guest", subject=GUEST, ip=IP)
    for _ in range(3):
        await store.refund(role="guest", subject=GUEST, ip=IP)

    usage = await store.snapshot(role="guest", subject=GUEST, ip=IP)
    assert usage.daily_remaining == 10


async def test_refund_without_consume_is_a_noop() -> None:
    store, _ = make_store()

    await store.refund(role="guest", subject="nunca-visto", ip=IP)

    usage = await store.snapshot(role="guest", subject="nunca-visto", ip=IP)
    assert usage.daily_remaining == 10


async def test_refund_after_day_rollover_is_a_noop() -> None:
    store, clock = make_store()

    await store.consume(role="guest", subject=GUEST, ip=IP)
    clock.advance(24 * 3600)
    await store.refund(role="guest", subject=GUEST, ip=IP)

    usage = await store.snapshot(role="guest", subject=GUEST, ip=IP)
    assert usage.daily_remaining == 10


async def test_refund_is_a_noop_for_admin() -> None:
    store, _ = make_store()

    await store.refund(role="admin", subject="admin", ip=IP)

    usage = await store.snapshot(role="admin", subject="admin", ip=IP)
    assert usage.per_minute_remaining == 60


async def test_daily_window_rolls_over_at_midnight_utc() -> None:
    clock = FakeClock(datetime(2026, 10, 28, 23, 59, 30, tzinfo=UTC))
    store, clock = make_store(clock)

    await store.consume(role="guest", subject=GUEST, ip=IP)
    before = await store.snapshot(role="guest", subject=GUEST, ip=IP)
    assert before.daily_remaining == 9
    assert before.resets_at == datetime(2026, 10, 29, 0, 0, tzinfo=UTC)

    clock.advance(60)
    after = await store.snapshot(role="guest", subject=GUEST, ip=IP)
    assert after.daily_remaining == 10
    assert after.resets_at == datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
    assert (await store.consume(role="guest", subject=GUEST, ip=IP)).allowed


async def test_reset_gid_and_ip() -> None:
    store, _ = make_store(guest_daily_messages=2, guest_ip_daily_messages=10)

    await store.consume(role="guest", subject=GUEST, ip=IP)
    await store.consume(role="guest", subject=GUEST, ip=IP)
    assert (await store.snapshot(role="guest", subject=GUEST, ip=IP)).daily_remaining == 0

    assert await store.reset(scope="ip", value=IP) is True
    assert (await store.snapshot(role="guest", subject=GUEST, ip=IP)).daily_remaining == 0

    assert await store.reset(scope="gid", value=GUEST) is True
    assert (await store.snapshot(role="guest", subject=GUEST, ip=IP)).daily_remaining == 2

    assert await store.reset(scope="gid", value="desconhecido") is False
    assert await store.reset(scope="ip", value="198.51.100.9") is False


async def test_reset_of_stale_day_is_not_found() -> None:
    store, clock = make_store()

    await store.consume(role="guest", subject=GUEST, ip=IP)
    clock.advance(24 * 3600)

    assert await store.reset(scope="gid", value=GUEST) is False
    assert await store.reset(scope="ip", value=IP) is False


async def test_list_guests_is_sorted_by_last_seen() -> None:
    store, clock = make_store()

    await store.consume(role="guest", subject="a", ip=IP)
    clock.advance(10)
    await store.consume(role="guest", subject="b", ip="198.51.100.7")

    guests = await store.list_guests()

    assert [guest.gid for guest in guests] == ["b", "a"]
    assert guests[0].ip == "198.51.100.7"
    assert guests[0].daily_used == 1
    assert guests[0].daily_limit == 10
    assert guests[0].last_seen_at == clock.now()

    assert await store.reset(scope="gid", value="a") is True
    again = await store.list_guests()
    assert next(guest for guest in again if guest.gid == "a").daily_used == 0


async def test_purge_expired_removes_stale_keys_and_keeps_active() -> None:
    clock = FakeClock(datetime(2026, 10, 28, 23, 59, 0, tzinfo=UTC))
    store, clock = make_store(clock)

    await store.consume(role="guest", subject=GUEST, ip=IP)
    assert await store.purge_expired() == 0

    clock.advance(24 * 3600)
    # Dois contadores diários + a janela de minuto expirada.
    assert await store.purge_expired() == 3
    # O guest ainda está dentro da retenção de 24 h.
    assert len(await store.list_guests()) == 1

    clock.advance(1)
    assert await store.purge_expired() == 1
    assert await store.list_guests() == []


async def test_concurrent_consumes_cannot_exceed_quota() -> None:
    store, _ = make_store(
        guest_daily_messages=3, guest_ip_daily_messages=100, guest_rate_per_min=100
    )

    decisions = await asyncio.gather(
        *(store.consume(role="guest", subject=GUEST, ip=IP) for _ in range(10))
    )

    allowed = [decision for decision in decisions if decision.allowed]
    denied = [decision for decision in decisions if not decision.allowed]
    assert len(allowed) == 3
    assert len(denied) == 7
    assert {decision.code for decision in denied} == {"guest_daily_limit_reached"}
    assert (await store.snapshot(role="guest", subject=GUEST, ip=IP)).daily_remaining == 0


async def test_concurrent_consumes_respect_ip_quota() -> None:
    store, _ = make_store(
        guest_daily_messages=10, guest_ip_daily_messages=3, guest_rate_per_min=100
    )

    decisions = await asyncio.gather(
        *(store.consume(role="guest", subject=f"gid-{index}", ip=IP) for index in range(10))
    )

    assert sum(1 for decision in decisions if decision.allowed) == 3
    assert {decision.code for decision in decisions if not decision.allowed} == {
        "ip_daily_limit_reached"
    }


async def test_lifespan_starts_and_stops_maintenance() -> None:
    store, _ = make_store(purge_interval_seconds=0.01)

    async with store.lifespan():
        await asyncio.sleep(0.03)
        assert store._task is not None

    assert store._task is None
    await store.aclose()


async def test_start_is_idempotent() -> None:
    store, _ = make_store(purge_interval_seconds=0.01)

    await store.start()
    await store.start()
    await store.aclose()


def test_rejects_invalid_purge_interval() -> None:
    with pytest.raises(ValueError):
        InMemoryUsageStore(make_settings(), FakeClock(), purge_interval_seconds=0)

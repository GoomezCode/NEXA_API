"""Testes da janela deslizante de login/emissão (F1-05)."""

from __future__ import annotations

import pytest

from app.core.rate_limit import SlidingWindowLimiter
from tests.conftest import FakeClock


async def test_allows_up_to_limit_and_blocks_next() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock, limit=3, window_seconds=60)

    first = await limiter.acquire("ip")
    second = await limiter.acquire("ip")
    third = await limiter.acquire("ip")
    blocked = await limiter.acquire("ip")

    assert [first.remaining, second.remaining, third.remaining] == [2, 1, 0]
    assert first.allowed and second.allowed and third.allowed
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after == 60


async def test_retry_after_decreases_with_time() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock, limit=1, window_seconds=60)
    await limiter.acquire("ip")

    clock.advance(20)
    status = await limiter.check("ip")
    assert status.allowed is False
    assert status.retry_after == 40

    clock.advance(40)
    assert (await limiter.check("ip")).allowed is True
    assert (await limiter.acquire("ip")).allowed is True


async def test_check_does_not_consume() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock, limit=2, window_seconds=60)

    assert (await limiter.check("ip")).remaining == 2
    assert (await limiter.check("ip")).remaining == 2
    assert (await limiter.acquire("ip")).remaining == 1
    assert (await limiter.check("ip")).remaining == 1


async def test_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter(FakeClock(), limit=1, window_seconds=60)
    assert (await limiter.acquire("a")).allowed is True
    assert (await limiter.acquire("a")).allowed is False
    assert (await limiter.acquire("b")).allowed is True


async def test_reset_clears_history() -> None:
    limiter = SlidingWindowLimiter(FakeClock(), limit=1, window_seconds=60)
    await limiter.acquire("ip")
    assert (await limiter.check("ip")).allowed is False

    await limiter.reset("ip")
    assert (await limiter.check("ip")).allowed is True


async def test_register_failure_caps_history_and_blocks() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock, limit=2, window_seconds=900)

    await limiter.register_failure("ip")
    await limiter.register_failure("ip")
    capped = await limiter.register_failure("ip")

    assert capped.allowed is False
    assert (await limiter.check("ip")).allowed is False

    clock.advance(900)
    assert (await limiter.check("ip")).allowed is True


async def test_purge_expired_keys() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock, limit=1, window_seconds=60)
    await limiter.acquire("old")
    clock.advance(61)
    await limiter.acquire("new")

    removed = await limiter.purge_expired()
    assert removed == 1
    # A chave ativa continua válida.
    assert (await limiter.check("new")).allowed is False


async def test_purge_forgets_inactive_keys() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock, limit=1, window_seconds=60)
    await limiter.acquire("ip")
    clock.advance(61)

    # A varredura periódica roda no próximo acesso e libera a chave.
    assert (await limiter.check("ip")).allowed is True


async def test_max_keys_is_enforced_on_burst() -> None:
    limiter = SlidingWindowLimiter(FakeClock(), limit=1, window_seconds=3600, max_keys=2)
    await limiter.acquire("a")
    await limiter.acquire("b")
    await limiter.acquire("c")

    # A chave mais antiga foi descartada; "a" volta a ter espaço.
    assert (await limiter.check("a")).allowed is True
    assert (await limiter.check("c")).allowed is False


def test_exposes_configuration() -> None:
    limiter = SlidingWindowLimiter(FakeClock(), limit=5, window_seconds=90)
    assert limiter.limit == 5
    assert limiter.window_seconds == 90


def test_rejects_invalid_configuration() -> None:
    clock = FakeClock()

    with pytest.raises(ValueError):
        SlidingWindowLimiter(clock, limit=0, window_seconds=60)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(clock, limit=1, window_seconds=0)

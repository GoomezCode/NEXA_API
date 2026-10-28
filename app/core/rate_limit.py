"""Limites por janela deslizante para login e emissão de tokens (RF-AUT-08, RNF-SEG-05)."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass

from app.core.clock import Clock

_PURGE_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class RateLimitStatus:
    """Resultado de uma consulta/consumo de limite."""

    allowed: bool
    remaining: int
    retry_after: int
    reset_at: float


class SlidingWindowLimiter:
    """Janela deslizante em memória, com no máximo `limit` marcas por chave.

    Adequada para um único processo (RP-07). As chaves expiram sozinhas: são
    podadas ao serem acessadas e também por uma varredura periódica (RD-03).
    """

    def __init__(
        self,
        clock: Clock,
        *,
        limit: int,
        window_seconds: float,
        max_keys: int = 10_000,
    ) -> None:
        if limit < 1:
            raise ValueError("limit deve ser >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds deve ser > 0")
        self._clock = clock
        self._limit = limit
        self._window = float(window_seconds)
        self._max_keys = max_keys
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._last_purge = 0.0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_seconds(self) -> float:
        return self._window

    async def check(self, key: str) -> RateLimitStatus:
        """Consulta o limite sem consumir uma marca."""
        async with self._lock:
            now = self._now()
            self._maybe_purge(now)
            return self._status(key, now)

    async def acquire(self, key: str) -> RateLimitStatus:
        """Consome uma marca se houver espaço; caso contrário, nega."""
        async with self._lock:
            now = self._now()
            self._maybe_purge(now)
            status = self._status(key, now)
            if not status.allowed:
                return status
            events = self._events.setdefault(key, deque())
            events.append(now)
            self._enforce_max_keys()
            return RateLimitStatus(
                allowed=True,
                remaining=max(0, self._limit - len(events)),
                retry_after=0,
                reset_at=events[0] + self._window,
            )

    async def register_failure(self, key: str) -> RateLimitStatus:
        """Registra uma falha (login) sem alterar o resultado da requisição atual."""
        async with self._lock:
            now = self._now()
            self._maybe_purge(now)
            events = self._events.setdefault(key, deque())
            self._prune(events, now)
            if len(events) < self._limit:
                events.append(now)
            return self._status(key, now)

    async def reset(self, key: str) -> None:
        """Remove o histórico da chave (ex.: login bem-sucedido)."""
        async with self._lock:
            self._events.pop(key, None)

    async def purge_expired(self) -> int:
        """Remove chaves sem marcas válidas e devolve quantas foram removidas."""
        async with self._lock:
            now = self._now()
            return self._purge(now)

    def _now(self) -> float:
        return self._clock.now().timestamp()

    def _prune(self, events: deque[float], now: float) -> None:
        threshold = now - self._window
        while events and events[0] <= threshold:
            events.popleft()

    def _status(self, key: str, now: float) -> RateLimitStatus:
        events = self._events.get(key)
        if events is not None:
            self._prune(events, now)
            if not events:
                self._events.pop(key, None)
                events = None

        if events is None:
            return RateLimitStatus(
                allowed=True,
                remaining=self._limit,
                retry_after=0,
                reset_at=now + self._window,
            )

        reset_at = events[0] + self._window
        if len(events) >= self._limit:
            return RateLimitStatus(
                allowed=False,
                remaining=0,
                retry_after=max(1, math.ceil(reset_at - now)),
                reset_at=reset_at,
            )
        return RateLimitStatus(
            allowed=True,
            remaining=self._limit - len(events),
            retry_after=0,
            reset_at=reset_at,
        )

    def _maybe_purge(self, now: float) -> None:
        if now - self._last_purge < _PURGE_INTERVAL_SECONDS:
            return
        self._last_purge = now
        self._purge(now)

    def _purge(self, now: float) -> int:
        threshold = now - self._window
        expired = [
            key for key, events in self._events.items() if not events or events[-1] <= threshold
        ]
        for key in expired:
            del self._events[key]

        # Salvaguarda de memória caso muitas chaves distintas apareçam antes da
        # próxima varredura: descarta as mais antigas (dict preserva inserção).
        self._enforce_max_keys()
        return len(expired)

    def _enforce_max_keys(self) -> None:
        overflow = len(self._events) - self._max_keys
        if overflow <= 0:
            return
        for key in list(self._events)[:overflow]:
            del self._events[key]


__all__ = ["RateLimitStatus", "SlidingWindowLimiter"]

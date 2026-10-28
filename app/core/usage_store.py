"""Contadores de uso em memória (RF-USO-01 a 12, RD-03).

O `UsageStore` é a interface; `InMemoryUsageStore` é a implementação da v1.1
(um processo, sem banco). Uma futura implementação em Redis só precisa cumprir
o mesmo protocolo.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol

from app.config import Settings
from app.core.clock import Clock
from app.core.usage import daily_reset_at, seconds_until
from app.schemas.usage import UsageInfo

MINUTE_WINDOW_SECONDS = 60.0
DEFAULT_PURGE_INTERVAL_SECONDS = 300.0

ResetScope = Literal["gid", "ip"]


@dataclass(frozen=True, slots=True)
class UsageDecision:
    """Resultado de `consume`, com o motivo da recusa quando houver."""

    allowed: bool
    usage: UsageInfo
    code: str | None = None
    retry_after: int = 0


@dataclass(frozen=True, slots=True)
class GuestUsage:
    """Linha de `GET /admin/guests` (RF-ADM-05)."""

    gid: str
    ip: str
    daily_used: int
    daily_limit: int
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class IpUsage:
    """Uso diário agregado por IP em `GET /admin/guests` (RF-ADM-05)."""

    ip: str
    daily_used: int
    daily_limit: int


class UsageStore(Protocol):
    """Interface de cotas; permite trocar a implementação sem tocar nas rotas."""

    async def snapshot(self, *, role: str, subject: str, ip: str) -> UsageInfo: ...

    async def consume(self, *, role: str, subject: str, ip: str) -> UsageDecision: ...

    async def refund(self, *, role: str, subject: str, ip: str) -> None: ...

    async def reset(self, *, scope: ResetScope, value: str) -> bool: ...

    async def list_guests(self) -> list[GuestUsage]: ...

    async def list_ip_usage(self) -> list[IpUsage]: ...

    async def purge_expired(self) -> int: ...

    async def start(self) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class _DailyCounter:
    day: date
    used: int


@dataclass(slots=True)
class _GuestRecord:
    ip: str
    last_seen: datetime


class InMemoryUsageStore:
    """Cotas por `gid`, IP e minuto, com janela diária fixa em 00:00 UTC."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        *,
        purge_interval_seconds: float = DEFAULT_PURGE_INTERVAL_SECONDS,
    ) -> None:
        if purge_interval_seconds <= 0:
            raise ValueError("purge_interval_seconds deve ser > 0")
        self._settings = settings
        self._clock = clock
        self._guest_daily_limit = settings.guest_daily_messages
        self._ip_daily_limit = settings.guest_ip_daily_messages
        self._guest_rate = settings.guest_rate_per_min
        self._admin_rate = settings.rate_limit_admin_chat_per_min
        self._retention = timedelta(hours=settings.guest_token_expire_hours)
        self._purge_interval = purge_interval_seconds

        self._lock = asyncio.Lock()
        self._gid_daily: dict[str, _DailyCounter] = {}
        self._ip_daily: dict[str, _DailyCounter] = {}
        self._guests: dict[str, _GuestRecord] = {}
        self._minute: dict[str, deque[float]] = {}
        self._task: asyncio.Task[None] | None = None

    # --- Consulta -----------------------------------------------------------
    async def snapshot(self, *, role: str, subject: str, ip: str) -> UsageInfo:
        """Limites e uso restante sem consumir cota."""
        async with self._lock:
            return self._snapshot(role=role, subject=subject, ip=ip, now=self._now())

    async def list_guests(self) -> list[GuestUsage]:
        """Guests conhecidos (com pelo menos uma requisição de chat)."""
        async with self._lock:
            today = self._now().date()
            entries = [
                GuestUsage(
                    gid=gid,
                    ip=record.ip,
                    daily_used=self._daily_used(self._gid_daily, gid, today),
                    daily_limit=self._guest_daily_limit,
                    last_seen_at=record.last_seen,
                )
                for gid, record in self._guests.items()
            ]
        return sorted(entries, key=lambda item: item.last_seen_at, reverse=True)

    async def list_ip_usage(self) -> list[IpUsage]:
        """Uso diário por IP, do mais usado para o menos usado."""
        async with self._lock:
            today = self._now().date()
            entries = [
                IpUsage(
                    ip=ip,
                    daily_used=counter.used,
                    daily_limit=self._ip_daily_limit,
                )
                for ip, counter in self._ip_daily.items()
                if counter.day == today
            ]
        return sorted(entries, key=lambda item: (-item.daily_used, item.ip))

    # --- Consumo/devolução --------------------------------------------------
    async def consume(self, *, role: str, subject: str, ip: str) -> UsageDecision:
        """Consome 1 unidade de forma atômica, verificando todas as cotas."""
        async with self._lock:
            now = self._now()
            if role == "admin":
                return self._consume_admin(subject=subject, now=now)

            today = now.date()
            gid_used = self._daily_used(self._gid_daily, subject, today)
            ip_used = self._daily_used(self._ip_daily, ip, today)

            # RF-USO-01/04 e RN-15: o gid tem prioridade quando ambos esgotaram.
            if gid_used >= self._guest_daily_limit:
                return self._denied(
                    "guest_daily_limit_reached",
                    role=role,
                    subject=subject,
                    ip=ip,
                    now=now,
                    retry_at=daily_reset_at(now),
                )
            if ip_used >= self._ip_daily_limit:
                return self._denied(
                    "ip_daily_limit_reached",
                    role=role,
                    subject=subject,
                    ip=ip,
                    now=now,
                    retry_at=daily_reset_at(now),
                )

            key = self._minute_key(role, subject)
            marks = self._prune_minute(key, now)
            if len(marks) >= self._guest_rate:
                retry_at = datetime.fromtimestamp(marks[0] + MINUTE_WINDOW_SECONDS, tz=UTC)
                return self._denied(
                    "rate_limited",
                    role=role,
                    subject=subject,
                    ip=ip,
                    now=now,
                    retry_at=retry_at,
                )

            self._increment(self._gid_daily, subject, today)
            self._increment(self._ip_daily, ip, today)
            marks.append(now.timestamp())
            self._guests[subject] = _GuestRecord(ip=ip, last_seen=now)
            return UsageDecision(
                allowed=True,
                usage=self._snapshot(role=role, subject=subject, ip=ip, now=now),
            )

    async def refund(self, *, role: str, subject: str, ip: str) -> None:
        """Devolve 1 unidade diária ao gid e ao IP (RF-USO-05)."""
        if role != "guest":
            return
        async with self._lock:
            today = self._now().date()
            self._decrement(self._gid_daily, subject, today)
            self._decrement(self._ip_daily, ip, today)

    # --- Administração ------------------------------------------------------
    async def reset(self, *, scope: ResetScope, value: str) -> bool:
        """Zera a cota diária de um `gid` ou IP; `False` se não existir."""
        async with self._lock:
            today = self._now().date()
            counters = self._gid_daily if scope == "gid" else self._ip_daily
            counter = counters.get(value)
            if counter is None or counter.day != today:
                return False
            counter.used = 0
            return True

    # --- Manutenção ---------------------------------------------------------
    async def purge_expired(self) -> int:
        """Remove chaves expiradas (virada do dia, janela e retenção)."""
        async with self._lock:
            now = self._now()
            today = now.date()
            removed = 0

            for counters in (self._gid_daily, self._ip_daily):
                for key in [k for k, v in counters.items() if v.day != today]:
                    del counters[key]
                    removed += 1

            for key in list(self._minute):
                marks = self._prune_minute(key, now)
                if not marks:
                    del self._minute[key]
                    removed += 1

            for gid in [
                gid
                for gid, record in self._guests.items()
                if now - record.last_seen > self._retention
            ]:
                del self._guests[gid]
                removed += 1

            return removed

    async def start(self) -> None:
        """Inicia a limpeza periódica em background."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._maintenance())

    async def aclose(self) -> None:
        """Encerra a limpeza periódica."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Context manager pronto para o `lifespan` da aplicação."""
        await self.start()
        try:
            yield
        finally:
            await self.aclose()

    async def _maintenance(self) -> None:
        while True:
            await asyncio.sleep(self._purge_interval)
            await self.purge_expired()

    # --- Internos -----------------------------------------------------------
    def _now(self) -> datetime:
        return self._clock.now().astimezone(UTC)

    def _consume_admin(self, *, subject: str, now: datetime) -> UsageDecision:
        key = self._minute_key("admin", subject)
        marks = self._prune_minute(key, now)
        if len(marks) >= self._admin_rate:
            retry_at = datetime.fromtimestamp(marks[0] + MINUTE_WINDOW_SECONDS, tz=UTC)
            return UsageDecision(
                allowed=False,
                usage=self._snapshot(role="admin", subject=subject, ip="", now=now),
                code="rate_limited",
                retry_after=seconds_until(retry_at, now=now),
            )
        marks.append(now.timestamp())
        return UsageDecision(
            allowed=True,
            usage=self._snapshot(role="admin", subject=subject, ip="", now=now),
        )

    def _denied(
        self,
        code: str,
        *,
        role: str,
        subject: str,
        ip: str,
        now: datetime,
        retry_at: datetime,
    ) -> UsageDecision:
        return UsageDecision(
            allowed=False,
            usage=self._snapshot(role=role, subject=subject, ip=ip, now=now),
            code=code,
            retry_after=seconds_until(retry_at, now=now),
        )

    def _snapshot(self, *, role: str, subject: str, ip: str, now: datetime) -> UsageInfo:
        if role == "admin":
            marks = self._peek_minute(self._minute_key(role, subject), now)
            return UsageInfo(
                daily_limit=None,
                daily_remaining=None,
                per_minute_limit=self._admin_rate,
                per_minute_remaining=max(0, self._admin_rate - marks),
                resets_at=None,
            )

        today = now.date()
        gid_used = self._daily_used(self._gid_daily, subject, today)
        ip_used = self._daily_used(self._ip_daily, ip, today)
        minute_used = self._peek_minute(self._minute_key(role, subject), now)
        return UsageInfo(
            daily_limit=self._guest_daily_limit,
            daily_remaining=max(
                0,
                min(
                    self._guest_daily_limit - gid_used,
                    self._ip_daily_limit - ip_used,
                ),
            ),
            per_minute_limit=self._guest_rate,
            per_minute_remaining=max(0, self._guest_rate - minute_used),
            resets_at=daily_reset_at(now),
        )

    def _minute_key(self, role: str, subject: str) -> str:
        return f"{role}:{subject}"

    def _peek_minute(self, key: str, now: datetime) -> int:
        marks = self._minute.get(key)
        if marks is None:
            return 0
        threshold = now.timestamp() - MINUTE_WINDOW_SECONDS
        return sum(1 for mark in marks if mark > threshold)

    def _prune_minute(self, key: str, now: datetime) -> deque[float]:
        marks = self._minute.setdefault(key, deque())
        threshold = now.timestamp() - MINUTE_WINDOW_SECONDS
        while marks and marks[0] <= threshold:
            marks.popleft()
        return marks

    @staticmethod
    def _daily_used(counters: dict[str, _DailyCounter], key: str, day: date) -> int:
        counter = counters.get(key)
        if counter is None or counter.day != day:
            return 0
        return counter.used

    @staticmethod
    def _increment(counters: dict[str, _DailyCounter], key: str, day: date) -> None:
        counter = counters.get(key)
        if counter is None or counter.day != day:
            counters[key] = _DailyCounter(day=day, used=1)
        else:
            counter.used += 1

    @staticmethod
    def _decrement(counters: dict[str, _DailyCounter], key: str, day: date) -> None:
        counter = counters.get(key)
        if counter is None or counter.day != day:
            return
        counter.used = max(0, counter.used - 1)


__all__ = [
    "DEFAULT_PURGE_INTERVAL_SECONDS",
    "MINUTE_WINDOW_SECONDS",
    "GuestUsage",
    "InMemoryUsageStore",
    "IpUsage",
    "ResetScope",
    "UsageDecision",
    "UsageStore",
]

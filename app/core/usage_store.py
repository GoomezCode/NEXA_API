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

ResetScope = Literal["gid", "ip", "uid"]


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

    async def snapshot(
        self,
        *,
        role: str,
        subject: str,
        ip: str,
        daily_limit: int | None = None,
        rate_limit: int | None = None,
    ) -> UsageInfo: ...

    async def consume(
        self,
        *,
        role: str,
        subject: str,
        ip: str,
        daily_limit: int | None = None,
        rate_limit: int | None = None,
    ) -> UsageDecision: ...

    async def refund(self, *, role: str, subject: str, ip: str) -> None: ...

    async def reset(self, *, scope: ResetScope, value: str) -> bool: ...

    async def list_guests(self) -> list[GuestUsage]: ...

    async def list_ip_usage(self) -> list[IpUsage]: ...

    async def purge_expired(self) -> int: ...

    async def start(self) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Limits:
    daily_limit: int
    ip_daily_limit: int
    rate_limit: int


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
    async def snapshot(
        self,
        *,
        role: str,
        subject: str,
        ip: str,
        daily_limit: int | None = None,
        rate_limit: int | None = None,
    ) -> UsageInfo:
        """Limites e uso restante sem consumir cota."""
        async with self._lock:
            return self._snapshot(
                role=role,
                subject=subject,
                ip=ip,
                now=self._now(),
                daily_limit=daily_limit,
                rate_limit=rate_limit,
            )

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
    async def consume(
        self,
        *,
        role: str,
        subject: str,
        ip: str,
        daily_limit: int | None = None,
        rate_limit: int | None = None,
    ) -> UsageDecision:
        """Consome 1 unidade de forma atômica, verificando todas as cotas."""
        async with self._lock:
            now = self._now()
            if role == "admin":
                return self._consume_admin(subject=subject, now=now, rate_limit=rate_limit)

            today = now.date()
            gid_used = self._daily_used(self._gid_daily, subject, today)
            limits = self._limits_for(role, daily_limit=daily_limit, rate_limit=rate_limit)

            # RF-USO-01/04 e RN-15: o gid tem prioridade quando ambos esgotaram.
            if role == "guest" and gid_used >= limits.daily_limit:
                return self._denied(
                    "guest_daily_limit_reached",
                    role=role,
                    subject=subject,
                    ip=ip,
                    now=now,
                    retry_at=daily_reset_at(now),
                    limits=limits,
                )
            if role == "user" and gid_used >= limits.daily_limit:
                return self._denied(
                    "user_daily_limit_reached",
                    role=role,
                    subject=subject,
                    ip=ip,
                    now=now,
                    retry_at=daily_reset_at(now),
                    limits=limits,
                )
            if role == "guest":
                ip_used = self._daily_used(self._ip_daily, ip, today)
                if ip_used >= limits.ip_daily_limit:
                    return self._denied(
                        "ip_daily_limit_reached",
                        role=role,
                        subject=subject,
                        ip=ip,
                        now=now,
                        retry_at=daily_reset_at(now),
                        limits=limits,
                    )

            key = self._minute_key(role, subject)
            marks = self._prune_minute(key, now)
            if len(marks) >= limits.rate_limit:
                retry_at = datetime.fromtimestamp(marks[0] + MINUTE_WINDOW_SECONDS, tz=UTC)
                return self._denied(
                    "rate_limited",
                    role=role,
                    subject=subject,
                    ip=ip,
                    now=now,
                    retry_at=retry_at,
                    limits=limits,
                )

            self._increment(self._gid_daily, subject, today)
            if role == "guest":
                self._increment(self._ip_daily, ip, today)
                self._guests[subject] = _GuestRecord(ip=ip, last_seen=now)
            marks.append(now.timestamp())
            return UsageDecision(
                allowed=True,
                usage=self._snapshot(
                    role=role,
                    subject=subject,
                    ip=ip,
                    now=now,
                    daily_limit=daily_limit,
                    rate_limit=rate_limit,
                ),
            )

    async def refund(self, *, role: str, subject: str, ip: str) -> None:
        """Devolve 1 unidade diária ao `gid`/`uid` (e ao IP, no guest)."""
        if role not in {"guest", "user"}:
            return
        async with self._lock:
            today = self._now().date()
            self._decrement(self._gid_daily, subject, today)
            if role == "guest":
                self._decrement(self._ip_daily, ip, today)

    # --- Administração ------------------------------------------------------
    async def reset(self, *, scope: ResetScope, value: str) -> bool:
        """Zera a cota diária de um `gid`, IP ou `uid`; `False` se não existir."""
        async with self._lock:
            today = self._now().date()
            counters = self._ip_daily if scope == "ip" else self._gid_daily
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

    def _consume_admin(
        self, *, subject: str, now: datetime, rate_limit: int | None
    ) -> UsageDecision:
        limit = rate_limit if rate_limit is not None else self._admin_rate
        key = self._minute_key("admin", subject)
        marks = self._prune_minute(key, now)
        if len(marks) >= limit:
            retry_at = datetime.fromtimestamp(marks[0] + MINUTE_WINDOW_SECONDS, tz=UTC)
            return UsageDecision(
                allowed=False,
                usage=self._snapshot(
                    role="admin", subject=subject, ip="", now=now, rate_limit=limit
                ),
                code="rate_limited",
                retry_after=seconds_until(retry_at, now=now),
            )
        marks.append(now.timestamp())
        return UsageDecision(
            allowed=True,
            usage=self._snapshot(role="admin", subject=subject, ip="", now=now, rate_limit=limit),
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
        limits: _Limits,
    ) -> UsageDecision:
        return UsageDecision(
            allowed=False,
            usage=self._snapshot(
                role=role,
                subject=subject,
                ip=ip,
                now=now,
                daily_limit=limits.daily_limit if role == "user" else None,
                rate_limit=limits.rate_limit if role == "user" else None,
            ),
            code=code,
            retry_after=seconds_until(retry_at, now=now),
        )

    def _limits_for(self, role: str, *, daily_limit: int | None, rate_limit: int | None) -> _Limits:
        if role == "user":
            return _Limits(
                daily_limit=(
                    self._settings.test_user_default_daily_messages
                    if daily_limit is None
                    else daily_limit
                ),
                ip_daily_limit=0,
                rate_limit=(
                    self._settings.test_user_default_rate_per_min
                    if rate_limit is None
                    else rate_limit
                ),
            )
        return _Limits(
            daily_limit=self._guest_daily_limit,
            ip_daily_limit=self._ip_daily_limit,
            rate_limit=self._guest_rate,
        )

    def _snapshot(
        self,
        *,
        role: str,
        subject: str,
        ip: str,
        now: datetime,
        daily_limit: int | None = None,
        rate_limit: int | None = None,
    ) -> UsageInfo:
        if role == "admin":
            limit = rate_limit if rate_limit is not None else self._admin_rate
            marks = self._peek_minute(self._minute_key(role, subject), now)
            return UsageInfo(
                daily_limit=None,
                daily_remaining=None,
                per_minute_limit=limit,
                per_minute_remaining=max(0, limit - marks),
                resets_at=None,
            )

        limits = self._limits_for(role, daily_limit=daily_limit, rate_limit=rate_limit)
        today = now.date()
        gid_used = self._daily_used(self._gid_daily, subject, today)
        minute_used = self._peek_minute(self._minute_key(role, subject), now)
        daily_remaining = max(0, limits.daily_limit - gid_used)
        if role == "guest":
            ip_used = self._daily_used(self._ip_daily, ip, today)
            daily_remaining = min(daily_remaining, max(0, limits.ip_daily_limit - ip_used))
        return UsageInfo(
            daily_limit=limits.daily_limit,
            daily_remaining=daily_remaining,
            per_minute_limit=limits.rate_limit,
            per_minute_remaining=max(0, limits.rate_limit - minute_used),
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

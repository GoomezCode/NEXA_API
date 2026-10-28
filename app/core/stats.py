"""Estatísticas de uso em memória desde a última inicialização (RF-ADM-02).

Os contadores são incrementais e não sobrevivem a um restart, como o SRS exige.
A contagem de guests distintos usa um conjunto com expiração e teto de tamanho,
para não crescer indefinidamente (RF-ADM-02).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.clock import Clock
from app.core.security import Role

#: Teto de guests distintos mantidos em memória (RF-ADM-02).
MAX_TRACKED_GUESTS = 10_000

#: Retenção padrão usada quando o chamador não informa outra.
DEFAULT_RETENTION_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    """Valores de `GET /admin/stats` (SRS §3.7.4)."""

    since: datetime
    chat_requests_total: int
    guest_distinct: int
    rate_limited_total: int
    provider_errors_total: int
    avg_response_seconds: float


class UsageStats:
    """Contadores de chat desde o boot, com guests distintos expiráveis."""

    def __init__(
        self,
        clock: Clock,
        *,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        max_guests: int = MAX_TRACKED_GUESTS,
    ) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds deve ser > 0")
        if max_guests <= 0:
            raise ValueError("max_guests deve ser > 0")
        self._clock = clock
        self._since = self._now()
        self._retention = timedelta(seconds=retention_seconds)
        self._max_guests = max_guests
        # Ordem de inserção = ordem de última atividade (entradas atualizadas vão
        # para o fim), o que permite descartar a mais antiga quando o teto é
        # atingido sem varrer o dicionário inteiro.
        self._guests: dict[str, datetime] = {}
        self._chat_requests = 0
        self._rate_limited = 0
        self._provider_errors = 0
        self._total_seconds = 0.0
        self._timed_requests = 0

    # --- Registro -----------------------------------------------------------
    def record_chat(self, *, subject: str, role: Role) -> None:
        """Conta uma requisição de chat; guests distintos são rastreados."""
        self._chat_requests += 1
        if role == "guest":
            self._touch_guest(subject)

    def record_rate_limited(self) -> None:
        """Conta uma mensagem barrada por cota ou limite por minuto."""
        self._rate_limited += 1

    def record_provider_error(self) -> None:
        """Conta uma falha do provedor (HTTP 502/504 ou evento `error`)."""
        self._provider_errors += 1

    def record_duration(self, seconds: float) -> None:
        """Acumula a duração de uma resposta concluída (sucesso ou erro)."""
        if seconds < 0:
            raise ValueError("seconds não pode ser negativo")
        self._total_seconds += seconds
        self._timed_requests += 1

    # --- Consulta -----------------------------------------------------------
    def snapshot(self) -> StatsSnapshot:
        """Fotografia consistente dos contadores, já sem guests expirados."""
        now = self._now()
        self._prune(now)
        average = self._total_seconds / self._timed_requests if self._timed_requests else 0.0
        return StatsSnapshot(
            since=self._since,
            chat_requests_total=self._chat_requests,
            guest_distinct=len(self._guests),
            rate_limited_total=self._rate_limited,
            provider_errors_total=self._provider_errors,
            avg_response_seconds=round(average, 2),
        )

    # --- Internos -----------------------------------------------------------
    def _now(self) -> datetime:
        return self._clock.now().astimezone(UTC)

    def _touch_guest(self, subject: str) -> None:
        now = self._now()
        self._guests.pop(subject, None)
        self._guests[subject] = now
        if len(self._guests) <= self._max_guests:
            return
        self._prune(now)
        while len(self._guests) > self._max_guests:
            oldest = next(iter(self._guests))
            del self._guests[oldest]

    def _prune(self, now: datetime) -> None:
        expired = [
            subject for subject, seen_at in self._guests.items() if now - seen_at > self._retention
        ]
        for subject in expired:
            del self._guests[subject]


__all__ = [
    "DEFAULT_RETENTION_SECONDS",
    "MAX_TRACKED_GUESTS",
    "StatsSnapshot",
    "UsageStats",
]

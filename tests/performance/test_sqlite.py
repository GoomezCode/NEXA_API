"""Planos de consulta e metas de performance do SQLite de usuários (F13-08, CA-43)."""

from __future__ import annotations

import math
import time
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import Settings
from app.db.connection import Database
from app.db.migrations import bootstrap_database
from app.db.models import UserRow
from app.db.repository import (
    CLEANUP_EXPIRED_SQL,
    LIST_USERS_SQL,
    USER_LOOKUP_SQL,
    UserRepository,
)
from tests.conftest import PASSWORD_HASH, FakeClock

#: Metas de p95 em milissegundos (docs/database-sqlite.md §1).
TARGETS = {"read": 2.0, "write": 10.0, "list": 20.0}

USERS = 10_000
READS = 500
WRITES = 200
LISTS = 30
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def database(settings: Settings, clock: FakeClock) -> AsyncIterator[Database]:
    db = Database(settings)
    await db.open()
    await bootstrap_database(db, settings, clock)
    try:
        yield db
    finally:
        await db.aclose()


@pytest.fixture
async def seeded(database: Database) -> UserRepository:
    repository = UserRepository(database)
    await repository.create_many(
        [
            UserRow(
                id=str(uuid4()),
                username=f"bench{index:05d}",
                username_display=f"bench{index:05d}",
                email=None,
                password_hash=PASSWORD_HASH,
                role="user",
                is_test=True,
                is_protected=False,
                is_active=True,
                daily_message_limit=100,
                rate_per_min=10,
                created_at=BASE_TIME + timedelta(microseconds=index),
                updated_at=BASE_TIME + timedelta(microseconds=index),
                last_login_at=None,
                expires_at=None,
            )
            for index in range(USERS)
        ]
    )
    return repository


def _percentile(samples: Sequence[float], quantile: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


async def _plan(database: Database, sql: str, params: tuple[object, ...]) -> str:
    rows = await database.fetch_all(f"EXPLAIN QUERY PLAN {sql}", params)
    return " | ".join(str(row["detail"]) for row in rows)


async def test_critical_queries_use_indexes(database: Database) -> None:
    lookup = await _plan(database, USER_LOOKUP_SQL, ("admin",))
    listing = await _plan(
        database,
        LIST_USERS_SQL,
        (None, None, None, None, None, None, None, None, None, 101),
    )
    cleanup = await _plan(database, CLEANUP_EXPIRED_SQL, ("2026-10-28T12:00:00.000000Z",))

    assert "ux_users_username" in lookup
    assert "ix_users_created_id" in listing
    assert "TEMP B-TREE" not in listing, "listagem não pode ordenar em memória"
    assert "ix_users_test_expires" in cleanup


async def test_p95_targets_with_ten_thousand_users(seeded: UserRepository) -> None:
    usernames = [f"bench{index:05d}" for index in range(USERS)]

    for name in usernames[:20]:
        await seeded.get_by_username(name)

    read_samples: list[float] = []
    for index in range(READS):
        mark = time.perf_counter()
        await seeded.get_by_username(usernames[index % len(usernames)])
        read_samples.append((time.perf_counter() - mark) * 1000)

    write_samples: list[float] = []
    for index in range(WRITES):
        row = await seeded.get_by_username(usernames[index % len(usernames)])
        assert row is not None
        mark = time.perf_counter()
        await seeded.update(row.id, {"daily_message_limit": 100 + index % 10})
        write_samples.append((time.perf_counter() - mark) * 1000)

    list_samples: list[float] = []
    for _ in range(LISTS):
        mark = time.perf_counter()
        await seeded.list_users(
            limit=100,
            cursor=None,
            role=None,
            is_test=None,
            is_active=None,
        )
        list_samples.append((time.perf_counter() - mark) * 1000)

    measured = {
        "read": _percentile(read_samples, 0.95),
        "write": _percentile(write_samples, 0.95),
        "list": _percentile(list_samples, 0.95),
    }
    exceeded = {
        name: (measured[name], TARGETS[name]) for name in TARGETS if measured[name] > TARGETS[name]
    }
    assert not exceeded, f"p95 acima da meta (medido, meta): {exceeded}"

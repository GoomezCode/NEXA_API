"""Benchmark do SQLite de usuários (F13-08).

Cria um banco temporário, insere 10 mil usuários e mede leituras por username,
escritas e listagens paginadas. Também imprime o `EXPLAIN QUERY PLAN` das
consultas críticas e retorna código 1 se alguma meta de p95 for excedida.

Uso:
    .venv/bin/python scripts/db_benchmark.py
    .venv/bin/python scripts/db_benchmark.py --users 10000
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

# Permite executar o script diretamente (`python scripts/db_benchmark.py`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.core.clock import SystemClock
from app.db.connection import Database
from app.db.migrations import bootstrap_database
from app.db.models import UserRow
from app.db.repository import (
    CLEANUP_EXPIRED_SQL,
    LIST_USERS_SQL,
    USER_LOOKUP_SQL,
    UserRepository,
)

# Hash sintético: o benchmark não autentica, só mede o SQLite.
PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$KMwCxpCk5TYd4cdcRrnSFA$"
    "QKtV/j4myVFG6IfaM7tR6yZnx4KqRu4Q+PtQA958h48"
)
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)

TARGETS = {
    "read": 2.0,
    "write": 10.0,
    "list": 20.0,
}


def _percentile(samples: Sequence[float], quantile: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _stats(samples: Sequence[float]) -> dict[str, float]:
    return {
        "p50": _percentile(samples, 0.50),
        "p95": _percentile(samples, 0.95),
        "p99": _percentile(samples, 0.99),
        "max": max(samples),
    }


def _settings(directory: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="development",
        admin_username="admin",
        admin_password_hash=PASSWORD_HASH,
        jwt_secret="benchmark-" + "x" * 32,
        database_url=f"sqlite:///{directory / 'nexa.db'}",
        db_backup_dir=str(directory / "backups"),
        db_startup_check="quick",
    )


def _rows(count: int) -> list[UserRow]:
    return [
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
        for index in range(count)
    ]


async def _explain(database: Database) -> None:
    queries = {
        "lookup por username": (USER_LOOKUP_SQL, ("x",)),
        "listagem por cursor": (
            LIST_USERS_SQL,
            (None, None, None, None, None, None, None, None, None, 101),
        ),
        "limpeza de expirados": (CLEANUP_EXPIRED_SQL, ("x",)),
    }
    print("\nEXPLAIN QUERY PLAN")
    for label, (sql, params) in queries.items():
        rows = await database.fetch_all(f"EXPLAIN QUERY PLAN {sql}", params)
        detail = " | ".join(str(row["detail"]) for row in rows)
        print(f"  {label}: {detail}")


async def run(users: int, reads: int, writes: int, lists: int) -> int:
    with tempfile.TemporaryDirectory(prefix="nexa-bench-") as temporary:
        directory = Path(temporary)
        settings = _settings(directory)
        clock = SystemClock()
        database = Database(settings)
        await database.open()
        try:
            await bootstrap_database(database, settings, clock)
            repository = UserRepository(database)
            rows = _rows(users)

            started = time.perf_counter()
            await repository.create_many(rows)
            insert_seconds = time.perf_counter() - started
            print(f"Seed: {users} usuários em {insert_seconds * 1000:.1f} ms")

            usernames = [row.username for row in rows]
            for name in usernames[:50]:
                await repository.get_by_username(name)

            read_samples: list[float] = []
            started = time.perf_counter()
            for index in range(reads):
                name = usernames[index % len(usernames)]
                mark = time.perf_counter()
                await repository.get_by_username(name)
                read_samples.append((time.perf_counter() - mark) * 1000)
            read_seconds = time.perf_counter() - started

            for row in rows[:20]:
                await repository.update(row.id, {"daily_message_limit": 101})

            write_samples: list[float] = []
            started = time.perf_counter()
            for index in range(writes):
                row = rows[index % len(rows)]
                mark = time.perf_counter()
                await repository.update(row.id, {"daily_message_limit": 100 + index % 10})
                write_samples.append((time.perf_counter() - mark) * 1000)
            write_seconds = time.perf_counter() - started

            for _ in range(5):
                await repository.list_users(
                    limit=100,
                    cursor=None,
                    role=None,
                    is_test=None,
                    is_active=None,
                )

            list_samples: list[float] = []
            started = time.perf_counter()
            for _ in range(lists):
                mark = time.perf_counter()
                await repository.list_users(
                    limit=100,
                    cursor=None,
                    role=None,
                    is_test=None,
                    is_active=None,
                )
                list_samples.append((time.perf_counter() - mark) * 1000)
            list_seconds = time.perf_counter() - started

            results = {
                "read": _stats(read_samples),
                "write": _stats(write_samples),
                "list": _stats(list_samples),
            }
            totals = {"read": read_seconds, "write": write_seconds, "list": list_seconds}
            counts = {
                "read": len(read_samples),
                "write": len(write_samples),
                "list": len(list_samples),
            }

            print("\nResultados")
            for name, stats in results.items():
                print(
                    f"  {name:5s} n={counts[name]:<5d} "
                    f"p50={stats['p50']:.3f} ms p95={stats['p95']:.3f} ms "
                    f"p99={stats['p99']:.3f} ms max={stats['max']:.3f} ms "
                    f"total={totals[name]:.2f} s"
                )

            await _explain(database)

            failures = [
                f"{name}: p95 {stats['p95']:.3f} ms > {TARGETS[name]:.1f} ms"
                for name, stats in results.items()
                if stats["p95"] > TARGETS[name]
            ]
            if failures:
                print("\nFALHOU")
                for failure in failures:
                    print(f"  - {failure}")
                return 1
            print("\nOK: todas as metas de p95 foram cumpridas.")
            print(f"Arquivo: {database.path} ({database.file_size()} bytes)")
            return 0
        finally:
            await database.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark do SQLite de usuários.")
    parser.add_argument("--users", type=int, default=10_000)
    parser.add_argument("--reads", type=int, default=10_000)
    parser.add_argument("--writes", type=int, default=1_000)
    parser.add_argument("--lists", type=int, default=100)
    args = parser.parse_args(argv)
    for name in ("users", "reads", "writes", "lists"):
        if getattr(args, name) <= 0:
            print(f"erro: --{name} deve ser > 0", file=sys.stderr)
            return 2
    return asyncio.run(run(args.users, args.reads, args.writes, args.lists))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

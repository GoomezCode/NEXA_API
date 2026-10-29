"""Ciclo de vida e falhas da conexão SQLite (F13-01)."""

from __future__ import annotations

import sqlite3
import stat
from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.db.connection import Database, DatabaseUnavailableError
from app.db.migrations import bootstrap_database
from app.db.models import UserRow
from app.db.repository import UserRepository
from tests.conftest import FakeClock


def _row() -> UserRow:
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    return UserRow(
        id="id-teste",
        username="teste01",
        username_display="teste01",
        email=None,
        password_hash="hash-qualquer",
        role="user",
        is_test=True,
        is_protected=False,
        is_active=True,
        daily_message_limit=100,
        rate_per_min=10,
        created_at=now,
        updated_at=now,
        last_login_at=None,
        expires_at=None,
    )


async def test_open_is_idempotent_and_secures_files(settings: Settings) -> None:
    database = Database(settings)
    assert await database.open() is database
    try:
        assert await database.open() is database
        assert database.is_open
        assert stat.S_IMODE(settings.database_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(settings.database_path.parent.stat().st_mode) == 0o700
        assert await database.journal_mode() == "wal"
        assert database.file_size() > 0
        await database.check_integrity("off")
    finally:
        await database.aclose()
    assert not database.is_open


async def test_closed_database_fails_closed(settings: Settings) -> None:
    database = Database(settings)

    assert await database.ping() is False
    assert await database.migration_version() == 0
    assert database.read_latency_ms_p95 == 0.0
    assert database.write_latency_ms_p95 == 0.0
    assert database.file_size() == 0
    with pytest.raises(DatabaseUnavailableError, match="não está aberto"):
        await database.journal_mode()
    with pytest.raises(DatabaseUnavailableError, match="sem conexões de leitura"):
        database.reader()
    with pytest.raises(DatabaseUnavailableError):
        async with database.write():
            pass


async def test_open_reports_unavailable_when_path_is_a_directory(settings: Settings) -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.database_path.mkdir()
    database = Database(settings)

    with pytest.raises(DatabaseUnavailableError):
        await database.open()

    assert not database.is_open


async def test_ping_and_version_degrade_when_sqlite_fails(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(settings)
    await database.open()

    async def failing_fetch(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("falha simulada")

    try:
        monkeypatch.setattr(database, "fetch_one", failing_fetch)
        assert await database.ping() is False
        assert await database.migration_version() == 0
    finally:
        await database.aclose()


async def test_backup_and_restore_roundtrip(settings: Settings, clock: FakeClock) -> None:
    database = Database(settings)
    await database.open()
    try:
        await bootstrap_database(database, settings, clock)
        repository = UserRepository(database)
        await repository.create(_row())

        backup = await database.backup(settings.database_path.parent / "backups" / "manual.db")

        assert backup.exists()
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert stat.S_IMODE(backup.parent.stat().st_mode) == 0o700

        assert await repository.delete_test_user("id-teste") is True
        assert await repository.get_by_username("teste01") is None

        await database.restore(backup)

        assert database.is_open
        assert await repository.get_by_username("teste01") is not None
    finally:
        await database.aclose()

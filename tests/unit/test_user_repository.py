"""Testes do SQLite de usuários: migrações, triggers, repositório e cache (F13)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.core.errors import ApiError
from app.core.security import hash_password
from app.core.users import UserService, generate_password
from app.db.connection import Database
from app.db.migrations import (
    AdminSeedError,
    AdminVerificationError,
    MigrationError,
    bootstrap_database,
    ensure_admin,
    load_migrations,
    run_migrations,
    verify_admin,
)
from app.db.models import ADMIN_ID, UserRow, to_db_time
from app.db.repository import (
    UserRepository,
    decode_cursor,
    encode_cursor,
)
from app.schemas.users import BulkCreateRequest, UserUpdate
from tests.conftest import FakeClock, make_settings

PASSWORD = "senha-de-teste"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
async def database(
    settings: Settings,
    clock: FakeClock,
) -> AsyncIterator[Database]:
    db = Database(settings)
    await db.open()
    await bootstrap_database(db, settings, clock)
    try:
        yield db
    finally:
        await db.aclose()


@pytest.fixture
def repository(database: Database) -> UserRepository:
    return UserRepository(database)


def _update_payload(**overrides: object) -> UserUpdate:
    return UserUpdate(**overrides)


def _row(
    username: str,
    *,
    password_hash: str = "hash-qualquer",
    email: str | None = None,
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
    is_active: bool = True,
) -> UserRow:
    return UserRow(
        id=f"id-{username}",
        username=username,
        username_display=username,
        email=email,
        password_hash=password_hash,
        role="user",
        is_test=True,
        is_protected=False,
        is_active=is_active,
        daily_message_limit=100,
        rate_per_min=10,
        created_at=created_at,
        updated_at=created_at,
        last_login_at=None,
        expires_at=expires_at,
    )


async def test_migrations_are_idempotent_and_seed_the_admin(
    database: Database,
    settings: Settings,
    clock: FakeClock,
) -> None:
    second = await run_migrations(database, settings, clock)
    admin = await verify_admin(database, settings)

    assert second.applied == ()
    assert second.current_version == 3
    assert admin.id == ADMIN_ID
    assert admin.username == "admin"
    assert admin.role == "admin"
    assert admin.is_protected
    assert not admin.is_test


async def test_migration_creates_backup_and_restore_preserves_data(
    database: Database,
    settings: Settings,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    async with database.write() as connection:
        await connection.execute("CREATE TABLE marcador (id INTEGER PRIMARY KEY)")

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "004_extra.sql").write_text("CREATE TABLE extra (id INTEGER);", encoding="utf-8")

    result = await run_migrations(
        database,
        settings.model_copy(update={"db_backup_before_migration": True}),
        clock,
        directory=migrations,
    )

    assert result.applied == (4,)
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.backup_path.stat().st_mode & 0o777 == 0o600

    await database.restore(result.backup_path)

    assert await database.migration_version() == 3
    marker = await database.fetch_one("SELECT name FROM sqlite_master WHERE name = 'marcador'")
    assert marker is not None
    assert await verify_admin(database, settings) is not None


async def test_only_users_and_migrations_are_stored(database: Database) -> None:
    rows = await database.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )

    assert {str(row["name"]) for row in rows} == {
        "users", "schema_migrations", "user_provider_keys"
    }


async def test_admin_cannot_be_deleted_or_demoted_by_sql(database: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="admin_protected"):
        async with database.write() as connection:
            await connection.execute("DELETE FROM users WHERE id = ?", (ADMIN_ID,))

    with pytest.raises(sqlite3.IntegrityError, match="admin_protected"):
        async with database.write() as connection:
            await connection.execute(
                "UPDATE users SET role = 'user' WHERE id = ?",
                (ADMIN_ID,),
            )

    with pytest.raises(sqlite3.IntegrityError, match="admin_protected"):
        async with database.write() as connection:
            await connection.execute(
                "UPDATE users SET is_active = 0 WHERE id = ?",
                (ADMIN_ID,),
            )


async def test_single_admin_and_protected_check_constraints(
    repository: UserRepository,
) -> None:
    second = replace(
        _row("outro-admin"),
        role="admin",
        is_test=False,
        is_protected=True,
    )

    with pytest.raises(sqlite3.IntegrityError, match="only_one_admin_allowed"):
        await repository.create(second)

    unprotected = replace(second, id="admin-2", username="admin2", is_protected=False)
    with pytest.raises(sqlite3.IntegrityError, match="admin_must_be_protected"):
        await repository.create(unprotected)


async def test_admin_seed_conflicts_with_existing_username(clock: FakeClock) -> None:
    settings = make_settings()
    database = Database(settings)
    await database.open()
    try:
        await run_migrations(database, settings, clock)
        await UserRepository(database).create(_row("admin"))

        with pytest.raises(AdminSeedError, match="admin_username_conflict"):
            await ensure_admin(database, settings, clock)
    finally:
        await database.aclose()


async def test_repository_crud_and_uniqueness(repository: UserRepository) -> None:
    created = await repository.create(_row("teste01", email="teste01@example.com"))

    assert await repository.get_by_username("TESTE01") == created
    assert await repository.get_by_id(created.id) == created
    assert await repository.get_by_id("inexistente") is None

    with pytest.raises(sqlite3.IntegrityError):
        await repository.create(_row("teste01", created_at=NOW + timedelta(seconds=1)))
    with pytest.raises(sqlite3.IntegrityError):
        await repository.create(
            _row("teste02", email="teste01@example.com", created_at=NOW + timedelta(seconds=2))
        )

    updated = await repository.update(
        created.id,
        {
            "username": "teste01b",
            "email": None,
            "updated_at": to_db_time(NOW + timedelta(minutes=1)),
        },
    )
    assert updated is not None
    assert updated.username == "teste01b"
    assert updated.email is None

    with pytest.raises(ValueError, match="não atualizáveis"):
        await repository.update(created.id, {"role": "admin"})

    assert await repository.update("inexistente", {"email": "x@example.com"}) is None


async def test_list_users_uses_cursor_and_filters(repository: UserRepository) -> None:
    rows = [
        _row(f"teste{index:02d}", created_at=NOW + timedelta(hours=1, seconds=index))
        for index in range(5)
    ]
    await repository.create_many(rows)

    first, cursor = await repository.list_users(
        limit=2, cursor=None, role="user", is_test=None, is_active=None
    )
    assert [row.username for row in first] == ["teste00", "teste01"]
    assert cursor is not None

    second, cursor = await repository.list_users(
        limit=2, cursor=decode_cursor(cursor), role="user", is_test=None, is_active=None
    )
    assert [row.username for row in second] == ["teste02", "teste03"]
    assert cursor is not None

    third, cursor = await repository.list_users(
        limit=2, cursor=decode_cursor(cursor), role="user", is_test=None, is_active=None
    )
    assert [row.username for row in third] == ["teste04"]
    assert cursor is None

    await repository.update(rows[2].id, {"is_active": 0})
    inactive, _ = await repository.list_users(
        limit=10, cursor=None, role="user", is_test=True, is_active=False
    )
    assert [row.username for row in inactive] == ["teste02"]


async def test_cleanup_removes_only_expired_test_users(
    repository: UserRepository,
    database: Database,
) -> None:
    expired = _row("expirado", created_at=NOW, expires_at=NOW - timedelta(minutes=1))
    alive = _row(
        "vivo",
        created_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(days=1),
    )
    permanent = _row("sem-expiração", created_at=NOW + timedelta(seconds=2))
    await repository.create_many([expired, alive, permanent])

    removed = await repository.cleanup_expired(NOW)

    assert removed == 1
    assert await repository.get_by_username("expirado") is None
    assert await repository.get_by_username("vivo") is not None
    assert await repository.get_by_username("sem-expiração") is not None
    assert await repository.get_by_id(ADMIN_ID) is not None
    assert await repository.stats() == (3, 2)


def test_cursor_roundtrip_and_validation() -> None:
    cursor = encode_cursor(NOW, "id-1")

    assert decode_cursor(cursor) == ("2026-10-28T12:00:00.000000Z", "id-1")
    for invalid in ("", "***", "sem-separador"):
        with pytest.raises(ValueError, match="cursor inválido"):
            decode_cursor(invalid)


async def test_admin_update_through_service_is_blocked(
    repository: UserRepository,
    settings: Settings,
    clock: FakeClock,
) -> None:
    service = UserService(settings, clock, repository)

    with pytest.raises(ApiError) as update_error:
        await service.update_user(ADMIN_ID, _update_payload())
    assert update_error.value.code == "admin_protected"

    with pytest.raises(ApiError) as delete_error:
        await service.delete_user(ADMIN_ID)
    assert delete_error.value.code == "admin_protected"

    with pytest.raises(ApiError) as reset_error:
        await service.reset_password(ADMIN_ID, None)
    assert reset_error.value.code == "admin_protected"


async def test_user_service_cache_invalidation(
    repository: UserRepository,
    settings: Settings,
    clock: FakeClock,
) -> None:
    service = UserService(settings, clock, repository)
    row = await repository.create(
        _row("teste-cache", password_hash=hash_password(PASSWORD), created_at=NOW)
    )

    assert await service.authenticate("teste-cache", PASSWORD) is not None
    assert await service.authenticate("teste-cache", "errada") is None
    assert await service.resolve_token(row.id) == row

    updated = await service.update_user(row.id, _update_payload(is_active=False))
    assert not updated.is_active
    with pytest.raises(ApiError):
        await service.resolve_token(row.id)
    assert await service.authenticate("teste-cache", PASSWORD) is None

    await service.update_user(row.id, _update_payload(is_active=True))
    assert await service.resolve_token(row.id) is not None

    reset, new_password = await service.reset_password(row.id, "nova-senha")
    assert new_password == "nova-senha"
    assert await service.authenticate("teste-cache", PASSWORD) is None
    assert await service.authenticate("teste-cache", "nova-senha") is not None

    await service.delete_user(reset.id)
    with pytest.raises(ApiError):
        await service.resolve_token(reset.id)


async def test_expired_user_is_rejected_even_with_valid_token(
    repository: UserRepository,
    settings: Settings,
    clock: FakeClock,
) -> None:
    service = UserService(settings, clock, repository)
    row = await repository.create(
        _row(
            "teste-expira",
            password_hash=hash_password(PASSWORD),
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
    )

    assert await service.resolve_token(row.id) == row
    clock.advance(600)

    with pytest.raises(ApiError):
        await service.resolve_token(row.id)
    assert await service.authenticate("teste-expira", PASSWORD) is None


async def test_cache_expiry_rereads_the_database(
    repository: UserRepository,
    settings: Settings,
    clock: FakeClock,
) -> None:
    service = UserService(settings, clock, repository)
    row = await repository.create(_row("teste-ttl", created_at=NOW))

    cached = await service.get_by_id(row.id)
    assert cached == row
    await repository.update(row.id, {"is_active": 0})
    assert (await service.get_by_id(row.id)) is cached  # ainda no cache

    clock.advance(settings.users_cache_ttl_seconds + 1)
    refreshed = await service.get_by_id(row.id)
    assert refreshed is not None
    assert refreshed is not cached
    assert not refreshed.is_active


async def test_generate_password_is_strong_and_unique() -> None:
    first = generate_password()
    second = generate_password()

    assert first.startswith("Nxa-")
    assert len(first) >= 20
    assert first != second


def test_load_migrations_rejects_invalid_and_duplicate_names(tmp_path: Path) -> None:
    (tmp_path / "sem-versao.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="nome de migração inválido"):
        load_migrations(tmp_path)

    (tmp_path / "sem-versao.sql").unlink()
    (tmp_path / "001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_b.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="versão de migração duplicada"):
        load_migrations(tmp_path)


async def test_checksum_divergence_blocks_boot(
    database: Database,
    settings: Settings,
    clock: FakeClock,
) -> None:
    async with database.write() as connection:
        await connection.execute(
            "UPDATE schema_migrations SET checksum = 'divergente' WHERE version = 1"
        )

    with pytest.raises(MigrationError, match="checksum divergente"):
        await run_migrations(database, settings, clock)


async def test_failing_migration_is_wrapped_and_rolled_back(
    settings: Settings,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    script = "CREATE TABLE parcial (id INTEGER);\nINSERT INTO ausente VALUES (1);\n"
    (tmp_path / "001_quebrada.sql").write_text(script, encoding="utf-8")
    database = Database(settings)
    await database.open()
    try:
        with pytest.raises(MigrationError, match="falha na migração 1"):
            await run_migrations(database, settings, clock, directory=tmp_path)

        rows = await database.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'parcial'"
        )
        assert rows == []
    finally:
        await database.aclose()


async def test_verify_admin_requires_a_seeded_admin(clock: FakeClock) -> None:
    settings = make_settings()
    database = Database(settings)
    await database.open()
    try:
        await run_migrations(database, settings, clock)

        with pytest.raises(AdminVerificationError, match="admin ausente"):
            await verify_admin(database, settings)
    finally:
        await database.aclose()


async def test_service_without_repository_fails_closed(
    settings: Settings,
    clock: FakeClock,
) -> None:
    service = UserService(settings, clock)

    assert service.available is False
    with pytest.raises(ApiError) as error:
        await service.get_by_username("teste01")
    assert error.value.code == "database_unavailable"
    assert await service.cleanup_expired() == 0
    await service.start()
    await service.aclose()


async def test_cleanup_loop_removes_expired_users(
    repository: UserRepository,
    settings: Settings,
    clock: FakeClock,
) -> None:
    service = UserService(settings, clock, repository, cleanup_interval_seconds=0.01)
    await repository.create(_row("expirado-loop", expires_at=NOW - timedelta(minutes=1)))

    await service.start()
    await service.start()  # idempotente
    await asyncio.sleep(0.1)
    await service.aclose()

    assert await repository.get_by_username("expirado-loop") is None


async def test_mark_login_failure_is_swallowed(
    repository: UserRepository,
    settings: Settings,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = UserService(settings, clock, repository)

    async def failing(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("disco cheio")

    monkeypatch.setattr(repository, "update_last_login", failing)

    await service.mark_login("qualquer-id")


async def test_database_errors_become_api_errors(
    repository: UserRepository,
    settings: Settings,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = UserService(settings, clock, repository)

    async def failing() -> tuple[int, int]:
        raise sqlite3.OperationalError("banco travado")

    monkeypatch.setattr(repository, "stats", failing)

    with pytest.raises(ApiError) as error:
        await service.stats()
    assert error.value.code == "database_unavailable"


async def test_bulk_respects_the_configured_maximum(
    repository: UserRepository,
    clock: FakeClock,
) -> None:
    service = UserService(make_settings(test_user_max_bulk=1), clock, repository)

    with pytest.raises(ApiError) as error:
        await service.create_bulk(BulkCreateRequest(prefix="lote", count=2))

    assert error.value.code == "validation_error"
    assert error.value.errors is not None
    assert error.value.errors[0].field == "count"


async def test_bulk_integrity_error_becomes_conflict(
    repository: UserRepository,
    settings: Settings,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = UserService(settings, clock, repository)

    async def failing(_: object) -> None:
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(repository, "create_many", failing)

    with pytest.raises(ApiError) as error:
        await service.create_bulk(BulkCreateRequest(prefix="lote", count=1))

    assert error.value.code == "username_conflict"


async def test_cache_can_be_disabled_and_is_bounded(
    repository: UserRepository,
    clock: FakeClock,
) -> None:
    disabled = UserService(make_settings(users_cache_ttl_seconds=0), clock, repository)
    first = await repository.create(_row("cache-off", created_at=NOW))
    assert await disabled.get_by_id(first.id) == first
    await repository.update(first.id, {"is_active": 0})
    refreshed = await disabled.get_by_id(first.id)
    assert refreshed is not None and not refreshed.is_active

    bounded = UserService(make_settings(users_cache_max_entries=1), clock, repository)
    second = await repository.create(_row("cache-one", created_at=NOW + timedelta(seconds=1)))
    third = await repository.create(_row("cache-two", created_at=NOW + timedelta(seconds=2)))
    await bounded.get_by_id(second.id)
    await bounded.get_by_id(third.id)
    assert len(bounded._cache) == 1

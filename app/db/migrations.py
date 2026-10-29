"""Runner de migrações SQL, backup e seed do administrador (F13-02/F13-04)."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.core.clock import Clock
from app.core.logging import get_logger
from app.db.connection import Database, DatabaseError
from app.db.models import ADMIN_ID, UserRow, to_db_time

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_PATTERN = re.compile(r"^(?P<version>\d+)_(?P<name>[a-z0-9_]+)\.sql$")

logger = get_logger("nexa.db")


class MigrationError(DatabaseError):
    """Migração ausente, fora de ordem, com checksum divergente ou SQL inválido."""


class AdminSeedError(DatabaseError):
    """Não foi possível semear o admin (conflito de username, por exemplo)."""


class AdminVerificationError(DatabaseError):
    """O admin não existe ou está inválido após a migração."""


@dataclass(frozen=True, slots=True)
class Migration:
    """Arquivo SQL versionado."""

    version: int
    name: str
    path: Path
    sql: str
    checksum: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Resultado do runner (`backup_path` alimenta a restauração pós-verificação)."""

    applied: tuple[int, ...]
    current_version: int
    backup_path: Path | None = None


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Carrega e ordena os arquivos `NNN_nome.sql`."""
    migrations: list[Migration] = []
    seen: set[int] = set()
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_PATTERN.match(path.name)
        if match is None:
            raise MigrationError(f"nome de migração inválido: {path.name}")
        version = int(match.group("version"))
        if version in seen:
            raise MigrationError(f"versão de migração duplicada: {version}")
        seen.add(version)
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                name=path.stem,
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    migrations.sort(key=lambda item: item.version)
    return migrations


def iter_statements(script: str) -> Iterator[str]:
    """Divide o script em comandos sem quebrar triggers/`BEGIN ... END`.

    `executescript` do `sqlite3` faz COMMIT implícito; separar os comandos
    permite rodar cada migração dentro da mesma transação (RF-USR-09).
    """
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    trailing = buffer.strip()
    if trailing:
        yield trailing


async def _applied_migrations(database: Database) -> dict[int, tuple[str, str]]:
    rows = await database.fetch_all(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    )
    return {int(row["version"]): (str(row["name"]), str(row["checksum"])) for row in rows}


async def run_migrations(
    database: Database,
    settings: Settings,
    clock: Clock,
    *,
    directory: Path = MIGRATIONS_DIR,
) -> MigrationResult:
    """Aplica as migrações pendentes e valida o checksum das já aplicadas."""
    migrations = load_migrations(directory)
    async with database.write() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version    INTEGER PRIMARY KEY,
              name       TEXT NOT NULL,
              checksum   TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )

    applied = await _applied_migrations(database)
    for migration in migrations:
        if migration.version not in applied:
            continue
        name, checksum = applied[migration.version]
        if checksum != migration.checksum:
            raise MigrationError(f"checksum divergente na migração {migration.version} ({name})")

    pending = [item for item in migrations if item.version not in applied]
    backup_path: Path | None = None
    if (
        pending
        and settings.db_backup_before_migration
        and database.path.exists()
        and database.path.stat().st_size > 0
    ):
        backup_path = await _backup(database, settings, clock)

    for migration in pending:
        try:
            async with database.write() as connection:
                for statement in iter_statements(migration.sql):
                    await connection.execute(statement)
                await connection.execute(
                    """
                    INSERT INTO schema_migrations (version, name, checksum, applied_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        migration.version,
                        migration.name,
                        migration.checksum,
                        to_db_time(clock.now()),
                    ),
                )
        except sqlite3.Error as exc:
            raise MigrationError(
                f"falha na migração {migration.version} ({migration.name}): {exc}"
            ) from exc
        logger.info("migration_applied", version=migration.version, name=migration.name)

    current = await database.migration_version()
    return MigrationResult(
        applied=tuple(item.version for item in pending),
        current_version=current,
        backup_path=backup_path,
    )


def _backup_directory(settings: Settings) -> Path:
    return Path(settings.db_backup_dir).expanduser()


async def _backup(database: Database, settings: Settings, clock: Clock) -> Path:
    directory = _backup_directory(settings)
    stamp = clock.now().strftime("%Y%m%dT%H%M%SZ")
    destination = directory / f"nexa-{stamp}.db"
    suffix = 0
    while destination.exists():
        suffix += 1
        destination = directory / f"nexa-{stamp}-{suffix}.db"
    path = await database.backup(destination)
    logger.info("database_backup_created", path=str(path))
    return path


async def ensure_admin(database: Database, settings: Settings, clock: Clock) -> None:
    """Semeia/atualiza o espelho do admin a partir da configuração (RF-USR-02)."""
    username = settings.admin_username.strip().lower()
    now = to_db_time(clock.now())
    sql = """
        INSERT INTO users (
          id, username, username_display, email, password_hash,
          role, is_test, is_protected, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, NULL, ?, 'admin', 0, 1, 1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          username = excluded.username,
          username_display = excluded.username_display,
          password_hash = excluded.password_hash,
          updated_at = excluded.updated_at
    """
    try:
        async with database.write() as connection:
            await connection.execute(
                sql,
                (
                    ADMIN_ID,
                    username,
                    settings.admin_username.strip(),
                    settings.admin_password_hash_value,
                    now,
                    now,
                ),
            )
    except sqlite3.IntegrityError as exc:
        message = str(exc)
        if "only_one_admin_allowed" in message or "admin_must_be_protected" in message:
            raise AdminSeedError(message) from exc
        raise AdminSeedError("admin_username_conflict") from exc
    logger.info("admin_seeded")


async def verify_admin(database: Database, settings: Settings) -> UserRow:
    """Confirma o admin após a migração; falha impede o boot (RF-USR-10)."""
    row = await database.fetch_one(
        "SELECT * FROM users WHERE id = ?",
        (ADMIN_ID,),
    )
    if row is None:
        raise AdminVerificationError("admin ausente após a migração")
    admin = UserRow.from_row(row)
    expected_username = settings.admin_username.strip().lower()
    problems: list[str] = []
    if admin.role != "admin":
        problems.append("role")
    if not admin.is_protected:
        problems.append("is_protected")
    if admin.is_test:
        problems.append("is_test")
    if not admin.is_active:
        problems.append("is_active")
    if admin.username != expected_username:
        problems.append("username")
    if admin.password_hash != settings.admin_password_hash_value:
        problems.append("password_hash")
    count_row = await database.fetch_one("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'")
    if count_row is None or int(count_row["total"]) != 1:
        problems.append("admin_count")
    if problems:
        raise AdminVerificationError("admin inválido após a migração: " + ", ".join(problems))
    return admin


async def bootstrap_database(
    database: Database,
    settings: Settings,
    clock: Clock,
    *,
    directory: Path = MIGRATIONS_DIR,
) -> tuple[MigrationResult, UserRow]:
    """Migra, semeia e verifica o admin; restaura backup se a verificação falhar."""
    result = await run_migrations(database, settings, clock, directory=directory)
    await ensure_admin(database, settings, clock)
    try:
        admin = await verify_admin(database, settings)
    except AdminVerificationError:
        if result.backup_path is not None:
            logger.error("admin_verification_failed_restoring_backup")
            await database.restore(result.backup_path)
        raise
    return result, admin


__all__ = [
    "MIGRATIONS_DIR",
    "AdminSeedError",
    "AdminVerificationError",
    "Migration",
    "MigrationError",
    "MigrationResult",
    "bootstrap_database",
    "ensure_admin",
    "iter_statements",
    "load_migrations",
    "run_migrations",
    "verify_admin",
]

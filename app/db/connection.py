"""Conexão SQLite, PRAGMAs, pool de leitura e escrita serializada (F13-01)."""

from __future__ import annotations

import asyncio
import math
import shutil
import sqlite3
import time
from collections import deque
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import Settings

#: Quantidade de amostras usadas para as latências p95 de `/admin/db/status`.
LATENCY_SAMPLES = 2048


class DatabaseError(Exception):
    """Erro base da camada de banco."""


class DatabaseUnavailableError(DatabaseError):
    """Banco fechado, inacessível ou em modo degradado."""


class DatabaseIntegrityError(DatabaseError):
    """`PRAGMA quick_check`/`integrity_check` reprovou."""


def _p95(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 3)


class Database:
    """Uma conexão de escrita serializada e N conexões de leitura.

    Todas as conexões usam `aiosqlite`; nenhuma chamada bloqueia o event loop
    (RNF-DES-03). As leituras podem ocorrer em paralelo graças ao WAL.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.path = settings.database_path
        self._writer: aiosqlite.Connection | None = None
        self._readers: list[aiosqlite.Connection] = []
        self._read_cursor = 0
        self._write_lock = asyncio.Lock()
        self._read_latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)
        self._write_latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)

    # --- Ciclo de vida ------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._writer is not None

    async def open(self) -> Database:
        """Cria diretório/arquivo, aplica PRAGMAs, abre o pool e verifica integridade."""
        if self.is_open:
            return self
        try:
            self._create_directory()
            writer = await aiosqlite.connect(str(self.path), isolation_level=None)
            writer.row_factory = aiosqlite.Row
            await self._apply_writer_pragmas(writer)
            self._writer = writer

            for _ in range(self._settings.sqlite_read_pool_size):
                reader = await aiosqlite.connect(str(self.path), isolation_level=None)
                reader.row_factory = aiosqlite.Row
                await self._apply_reader_pragmas(reader)
                self._readers.append(reader)
        except (sqlite3.Error, OSError) as exc:
            await self.aclose()
            raise DatabaseUnavailableError(str(exc)) from exc

        self._secure_files()
        await self.check_integrity(self._settings.db_startup_check)
        return self

    async def aclose(self) -> None:
        """Fecha o pool; opcionalmente executa `PRAGMA optimize` (RNF-DES-12)."""
        if self._writer is not None and self._settings.db_optimize_on_shutdown:
            with suppress(sqlite3.Error):
                await self._writer.execute("PRAGMA optimize")

        readers, self._readers = self._readers, []
        for reader in readers:
            with suppress(sqlite3.Error):
                await reader.close()

        writer, self._writer = self._writer, None
        if writer is not None:
            with suppress(sqlite3.Error):
                await writer.close()
        self._secure_files()

    # --- Acesso -------------------------------------------------------------
    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        """Transação curta de escrita (`BEGIN IMMEDIATE`) com lock assíncrono."""
        writer = self._require_writer()
        started = time.perf_counter()
        async with self._write_lock:
            await writer.execute("BEGIN IMMEDIATE")
            try:
                yield writer
            except BaseException:
                await writer.rollback()
                raise
            else:
                await writer.commit()
                self._write_latencies.append((time.perf_counter() - started) * 1000)

    def reader(self) -> aiosqlite.Connection:
        """Conexão de leitura em round-robin (WAL evita bloqueio global)."""
        if not self._readers:
            raise DatabaseUnavailableError("banco sem conexões de leitura")
        connection = self._readers[self._read_cursor % len(self._readers)]
        self._read_cursor += 1
        return connection

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        connection = self.reader()
        started = time.perf_counter()
        cursor = await connection.execute(sql, tuple(params))
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()
            self._read_latencies.append((time.perf_counter() - started) * 1000)

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        connection = self.reader()
        started = time.perf_counter()
        cursor = await connection.execute(sql, tuple(params))
        try:
            return list(await cursor.fetchall())
        finally:
            await cursor.close()
            self._read_latencies.append((time.perf_counter() - started) * 1000)

    async def ping(self) -> bool:
        """Verifica que o banco responde a uma consulta trivial."""
        if not self.is_open:
            return False
        try:
            row = await self.fetch_one("SELECT 1 AS ok")
        except (sqlite3.Error, DatabaseError):
            return False
        return row is not None

    # --- Manutenção ---------------------------------------------------------
    async def check_integrity(self, mode: str) -> None:
        """`quick`/`full` no boot; `off` desabilita (RF-USR-09)."""
        if mode == "off":
            return
        pragma = "quick_check" if mode == "quick" else "integrity_check"
        writer = self._require_writer()
        try:
            cursor = await writer.execute(f"PRAGMA {pragma}")
            rows = await cursor.fetchall()
            await cursor.close()
        except sqlite3.Error as exc:
            raise DatabaseIntegrityError(str(exc)) from exc
        values = [str(row[0]) for row in rows]
        if values != ["ok"]:
            raise DatabaseIntegrityError("; ".join(values))

    async def journal_mode(self) -> str:
        writer = self._require_writer()
        cursor = await writer.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        await cursor.close()
        return str(row[0]).lower() if row is not None else "unknown"

    async def migration_version(self) -> int:
        if not self.is_open:
            return 0
        try:
            row = await self.fetch_one(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            )
        except sqlite3.Error:
            return 0
        return int(row["version"]) if row is not None else 0

    async def backup(self, destination: Path) -> Path:
        """Backup consistente com o WAL ativo (RF-USR-09, RD-18)."""
        writer = self._require_writer()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.chmod(0o700)
        target = await aiosqlite.connect(str(destination))
        try:
            await writer.backup(target)
        finally:
            await target.close()
        await asyncio.to_thread(destination.chmod, 0o600)
        return destination

    async def restore(self, backup: Path) -> None:
        """Restaura um backup e reabre o pool (usado só no boot)."""
        await self.aclose()
        await asyncio.to_thread(shutil.copy2, backup, self.path)
        for suffix in ("-wal", "-shm"):
            auxiliary = self.path.with_name(f"{self.path.name}{suffix}")
            auxiliary.unlink(missing_ok=True)
        await self.open()

    # --- Diagnóstico --------------------------------------------------------
    @property
    def read_latency_ms_p95(self) -> float:
        return _p95(list(self._read_latencies))

    @property
    def write_latency_ms_p95(self) -> float:
        return _p95(list(self._write_latencies))

    def file_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    # --- Internos -----------------------------------------------------------
    def _require_writer(self) -> aiosqlite.Connection:
        if self._writer is None:
            raise DatabaseUnavailableError("banco não está aberto")
        return self._writer

    def _create_directory(self) -> None:
        parent = self.path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            parent.chmod(0o700)

    async def _apply_writer_pragmas(self, connection: aiosqlite.Connection) -> None:
        if self._settings.sqlite_wal:
            cursor = await connection.execute("PRAGMA journal_mode=WAL")
            await cursor.fetchone()
            await cursor.close()
        await connection.execute(f"PRAGMA synchronous={self._settings.sqlite_synchronous}")
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute(f"PRAGMA busy_timeout={self._settings.sqlite_busy_timeout_ms}")
        await connection.execute(f"PRAGMA cache_size=-{self._settings.sqlite_cache_size_kb}")
        await connection.execute(f"PRAGMA mmap_size={self._settings.sqlite_mmap_bytes}")
        await connection.execute("PRAGMA temp_store=MEMORY")
        await connection.execute("PRAGMA wal_autocheckpoint=1000")

    async def _apply_reader_pragmas(self, connection: aiosqlite.Connection) -> None:
        await connection.execute("PRAGMA query_only=ON")
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute(f"PRAGMA busy_timeout={self._settings.sqlite_busy_timeout_ms}")
        await connection.execute(f"PRAGMA cache_size=-{self._settings.sqlite_cache_size_kb}")
        await connection.execute(f"PRAGMA mmap_size={self._settings.sqlite_mmap_bytes}")
        await connection.execute("PRAGMA temp_store=MEMORY")

    def _secure_files(self) -> None:
        for candidate in (
            self.path,
            self.path.with_name(f"{self.path.name}-wal"),
            self.path.with_name(f"{self.path.name}-shm"),
        ):
            try:
                candidate.chmod(0o600)
            except OSError:
                continue


__all__ = [
    "LATENCY_SAMPLES",
    "Database",
    "DatabaseError",
    "DatabaseIntegrityError",
    "DatabaseUnavailableError",
]

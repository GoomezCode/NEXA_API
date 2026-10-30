"""Camada SQLite exclusiva de usuários e migrações (SRS v2.0, §3.9)."""

from app.db.connection import (
    Database,
    DatabaseError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)
from app.db.models import ADMIN_ID, UserRole, UserRow, from_db_time, to_db_time
from app.db.repository import (
    MAX_PAGE_SIZE,
    USER_COLUMNS,
    UserRepository,
    decode_cursor,
    encode_cursor,
)

__all__ = [
    "ADMIN_ID",
    "MAX_PAGE_SIZE",
    "USER_COLUMNS",
    "Database",
    "DatabaseError",
    "DatabaseIntegrityError",
    "DatabaseUnavailableError",
    "UserRepository",
    "UserRole",
    "UserRow",
    "decode_cursor",
    "encode_cursor",
    "from_db_time",
    "to_db_time",
]

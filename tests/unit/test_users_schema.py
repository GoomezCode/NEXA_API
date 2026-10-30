"""Validação dos schemas de usuários (F13-06)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.users import UserCreate, UserUpdate, iso_utc


def test_iso_utc_normalizes_naive_and_aware_values() -> None:
    assert iso_utc(None) is None
    assert iso_utc(datetime(2026, 10, 28, 12, 0)) == "2026-10-28T12:00:00Z"
    assert iso_utc(datetime(2026, 10, 28, 12, 0, tzinfo=UTC)) == "2026-10-28T12:00:00Z"
    assert iso_utc(datetime(2026, 10, 28, 9, 0, tzinfo=UTC)) == "2026-10-28T09:00:00Z"


def test_user_create_normalizes_username_and_email() -> None:
    created = UserCreate(username="  Teste01  ", email="  TESTE@Example.COM ")

    assert created.username == "Teste01"
    assert created.email == "teste@example.com"
    assert UserCreate(username="teste01", email="   ").email is None
    assert UserCreate(username="teste01", email=None).email is None


def test_user_create_rejects_invalid_username_and_email() -> None:
    for username in ("-comeca-com-hifen", "com espaço", "com/barra"):
        with pytest.raises(ValidationError, match="use 3-64 caracteres"):
            UserCreate(username=username)
    with pytest.raises(ValidationError):
        UserCreate(username="ab")

    with pytest.raises(ValidationError, match="e-mail inválido"):
        UserCreate(username="teste01", email="sem-arroba")


def test_user_create_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        UserCreate(username="teste01", role="admin")


def test_user_update_optional_validators() -> None:
    assert UserUpdate().username is None
    assert UserUpdate().email is None
    assert UserUpdate(username=None, email=None) == UserUpdate()
    assert UserUpdate(username="  Teste02  ").username == "Teste02"
    assert UserUpdate(email="  TESTE@Example.COM ").email == "teste@example.com"
    assert UserUpdate(email="   ").email is None

    with pytest.raises(ValidationError, match="use 3-64 caracteres"):
        UserUpdate(username="-hifen")
    with pytest.raises(ValidationError, match="e-mail inválido"):
        UserUpdate(email="sem-arroba")

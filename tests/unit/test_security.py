"""Testes de hash, JWT e AuthService (F1-01/F1-02)."""

from __future__ import annotations

from datetime import UTC, datetime

import bcrypt
import jwt
import pytest

from app.core.auth import AuthService
from app.core.errors import ApiError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from tests.conftest import FakeClock, make_settings

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def test_hash_and_verify_password() -> None:
    hashed = hash_password("senha-secreta")
    assert hashed.startswith("$argon2id$")
    assert verify_password("senha-secreta", hashed) is True
    assert verify_password("outra", hashed) is False


def test_verify_password_accepts_bcrypt() -> None:
    hashed = bcrypt.hashpw(b"senha", bcrypt.gensalt(rounds=4)).decode()
    assert verify_password("senha", hashed) is True
    assert verify_password("errada", hashed) is False


def test_verify_password_rejects_malformed_hashes() -> None:
    assert verify_password("x", "$argon2id$invalido") is False
    assert verify_password("x", "texto-puro") is False
    assert verify_password("x", "$2b$invalido") is False


def test_verify_password_handles_bcrypt_length_limit() -> None:
    hashed = bcrypt.hashpw(b"senha", bcrypt.gensalt(rounds=4)).decode()
    assert verify_password("x" * 100, hashed) is False


def test_create_and_decode_token() -> None:
    settings = make_settings()
    token = create_access_token(settings, subject="admin", role="admin", expires_in=600, now=NOW)
    payload = decode_access_token(token, settings, now=NOW)
    assert payload.subject == "admin"
    assert payload.role == "admin"

    raw = jwt.decode(
        token,
        settings.jwt_secret_value,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": False},
    )
    assert raw["sub"] == "admin"
    assert raw["role"] == "admin"
    assert raw["exp"] - raw["iat"] == 600


def test_decode_expired_token() -> None:
    settings = make_settings()
    token = create_access_token(settings, subject="admin", role="admin", expires_in=-10, now=NOW)
    with pytest.raises(ApiError) as exc_info:
        decode_access_token(token, settings, now=NOW)
    assert exc_info.value.code == "token_expired"
    assert exc_info.value.status_code == 401


def test_decode_rejects_invalid_signature() -> None:
    settings = make_settings()
    other = make_settings(jwt_secret="z" * 40)
    token = create_access_token(other, subject="admin", role="admin", expires_in=600, now=NOW)
    with pytest.raises(ApiError) as exc_info:
        decode_access_token(token, settings, now=NOW)
    assert exc_info.value.code == "invalid_token"


def test_decode_rejects_unknown_role() -> None:
    settings = make_settings()
    issued_at = int(NOW.timestamp())
    token = jwt.encode(
        {"sub": "x", "role": "root", "iat": issued_at, "exp": issued_at + 600},
        settings.jwt_secret_value,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ApiError) as exc_info:
        decode_access_token(token, settings, now=NOW)
    assert exc_info.value.code == "invalid_token"


def test_decode_rejects_missing_claims() -> None:
    settings = make_settings()
    issued_at = int(NOW.timestamp())
    token = jwt.encode(
        {"sub": "x", "role": "guest", "exp": issued_at + 600},
        settings.jwt_secret_value,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ApiError) as exc_info:
        decode_access_token(token, settings, now=NOW)
    assert exc_info.value.code == "invalid_token"


@pytest.mark.parametrize(
    "payload",
    [
        {"sub": "x", "role": "guest", "iat": int(NOW.timestamp()), "exp": "abc"},
        {
            "sub": "",
            "role": "guest",
            "iat": int(NOW.timestamp()),
            "exp": int(NOW.timestamp()) + 600,
        },
        {"sub": "x", "role": 123, "iat": int(NOW.timestamp()), "exp": int(NOW.timestamp()) + 600},
    ],
)
def test_decode_rejects_invalid_claim_types(payload: dict[str, object]) -> None:
    settings = make_settings()
    token = jwt.encode(payload, settings.jwt_secret_value, algorithm=settings.jwt_algorithm)
    with pytest.raises(ApiError) as exc_info:
        decode_access_token(token, settings, now=NOW)
    assert exc_info.value.code == "invalid_token"


def test_decode_rejects_garbage() -> None:
    with pytest.raises(ApiError) as exc_info:
        decode_access_token("not-a-jwt", make_settings())
    assert exc_info.value.code == "invalid_token"


async def test_auth_service_accepts_valid_credentials() -> None:
    settings = make_settings()
    service = AuthService(settings, FakeClock(NOW))
    await service.authenticate("admin", "admin")


async def test_auth_service_rejects_wrong_password() -> None:
    service = AuthService(make_settings(), FakeClock(NOW))
    with pytest.raises(ApiError) as exc_info:
        await service.authenticate("admin", "errada")
    assert exc_info.value.code == "invalid_credentials"


async def test_auth_service_rejects_unknown_user() -> None:
    service = AuthService(make_settings(), FakeClock(NOW))
    with pytest.raises(ApiError) as exc_info:
        await service.authenticate("outro", "admin")
    assert exc_info.value.code == "invalid_credentials"


async def test_auth_service_uses_dummy_hash_for_unknown_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.auth as auth_module

    seen: list[str] = []

    def fake_verify(password: str, password_hash: str) -> bool:
        seen.append(password_hash)
        return True

    monkeypatch.setattr(auth_module, "verify_password", fake_verify)
    settings = make_settings()
    service = AuthService(settings, FakeClock(NOW))

    with pytest.raises(ApiError):
        await service.authenticate("outro", "admin")
    assert seen and seen[0] != settings.admin_password_hash_value


async def test_auth_service_uses_configured_hash_for_known_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.auth as auth_module

    seen: list[str] = []

    def fake_verify(password: str, password_hash: str) -> bool:
        seen.append(password_hash)
        return True

    monkeypatch.setattr(auth_module, "verify_password", fake_verify)
    settings = make_settings()
    service = AuthService(settings, FakeClock(NOW))

    await service.authenticate("admin", "admin")
    assert seen == [settings.admin_password_hash_value]


def test_issue_tokens_have_configured_expiry() -> None:
    settings = make_settings(admin_token_expire_minutes=5, guest_token_expire_hours=2)
    service = AuthService(settings, FakeClock(NOW))

    admin_token, admin_expires = service.issue_admin_token()
    guest_token, guest_expires = service.issue_guest_token("gid-1")

    assert admin_expires == 300
    assert guest_expires == 7200
    assert decode_access_token(admin_token, settings, now=NOW).role == "admin"
    guest_payload = decode_access_token(guest_token, settings, now=NOW)
    assert guest_payload.role == "guest"
    assert guest_payload.subject == "gid-1"

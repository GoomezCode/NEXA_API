"""Testes de configuração fail-fast (F0-02)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import get_settings
from tests.conftest import PASSWORD_HASH, make_settings


def test_defaults() -> None:
    settings = make_settings()
    assert settings.environment == "development"
    assert settings.jwt_secret_value == "x" * 40
    assert settings.admin_password_hash_value == PASSWORD_HASH
    assert settings.is_production is False
    assert settings.cors_origins == ["http://localhost:5173"]
    assert settings.trusted_proxies == []
    assert settings.uvicorn_workers == 1


def test_lists_accept_comma_separated_values() -> None:
    settings = make_settings(
        cors_origins="http://a.test, http://b.test ,",
        trusted_proxies="127.0.0.1/32,10.0.0.0/8",
    )
    assert settings.cors_origins == ["http://a.test", "http://b.test"]
    assert settings.trusted_proxies == ["127.0.0.1/32", "10.0.0.0/8"]


def test_log_level_is_normalized() -> None:
    assert make_settings(log_level="debug").log_level == "DEBUG"
    assert make_settings(log_level="warning").log_level == "WARNING"


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(log_level="VERBOSE")


def test_password_hash_must_have_supported_prefix() -> None:
    with pytest.raises(ValidationError):
        make_settings(admin_password_hash="plain-text")


def test_bcrypt_hash_is_accepted() -> None:
    settings = make_settings(admin_password_hash="$2b$12$abcdefghijklmnopqrstuv")
    assert settings.admin_password_hash_value.startswith("$2b$")


def test_empty_admin_username_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(admin_username="   ")


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_hmac_algorithms_are_accepted(algorithm: str) -> None:
    assert make_settings(jwt_algorithm=algorithm).jwt_algorithm == algorithm


def test_asymmetric_algorithm_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(jwt_algorithm="RS256")


def test_production_requires_long_secret() -> None:
    with pytest.raises(ValidationError):
        make_settings(environment="production", jwt_secret="curto")


def test_production_rejects_example_secret() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            environment="production",
            jwt_secret="troque-por-um-segredo-aleatorio-de-32-bytes-ou-mais",
        )


def test_production_accepts_strong_secret() -> None:
    settings = make_settings(environment="production", jwt_secret="a" * 48)
    assert settings.is_production is True


def test_multiple_workers_require_redis() -> None:
    with pytest.raises(ValidationError):
        make_settings(uvicorn_workers=2)


def test_multiple_workers_with_redis_are_allowed() -> None:
    settings = make_settings(uvicorn_workers=2, redis_url="redis://localhost:6379/0")
    assert settings.uvicorn_workers == 2
    assert settings.redis_url == "redis://localhost:6379/0"


def test_body_limit_must_fit_largest_admin_payload() -> None:
    with pytest.raises(ValidationError):
        make_settings(max_request_body_bytes=100, max_messages_per_request=10, max_message_chars=50)


def test_body_limit_equal_to_largest_payload_is_allowed() -> None:
    settings = make_settings(
        max_request_body_bytes=500,
        max_messages_per_request=10,
        max_message_chars=50,
    )
    assert settings.max_request_body_bytes == 500


def test_cloud_provider_requires_provider() -> None:
    with pytest.raises(ValidationError):
        make_settings(ai_provider="cloud")


def test_cloud_provider_requires_model() -> None:
    with pytest.raises(ValidationError):
        make_settings(ai_provider="cloud", cloud_provider="openai")


def test_cloud_provider_complete_is_allowed() -> None:
    settings = make_settings(
        ai_provider="cloud",
        cloud_provider="openai",
        cloud_model="gpt-4o-mini",
        cloud_api_key="sk-test",
    )
    assert settings.cloud_provider == "openai"
    assert settings.cloud_model == "gpt-4o-mini"


def test_invalid_ollama_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(ollama_url="localhost:11434")


def test_get_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ADMIN_USERNAME", "root")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("JWT_SECRET", "y" * 40)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.admin_username == "root"
    assert first.jwt_secret_value == "y" * 40
    get_settings.cache_clear()

"""Testes de configuração fail-fast (F0-02)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from tests.conftest import PASSWORD_HASH, make_settings


def test_defaults() -> None:
    settings = make_settings()
    assert settings.environment == "development"
    assert settings.jwt_secret_value == "x" * 40
    assert settings.admin_password_hash_value == PASSWORD_HASH
    assert settings.is_production is False
    assert settings.cors_origins == ["http://localhost:5173"]
    assert settings.trusted_proxies == []
    assert settings.ai_allowed_models == []
    assert settings.models_cache_seconds == 10
    assert settings.uvicorn_workers == 1


def test_lists_accept_comma_separated_values() -> None:
    settings = make_settings(
        cors_origins="http://a.test, http://b.test ,",
        trusted_proxies="127.0.0.1/32,10.0.0.0/8",
        ai_allowed_models="llama3.1, qwen3-coder:latest ,",
    )
    assert settings.cors_origins == ["http://a.test", "http://b.test"]
    assert settings.trusted_proxies == ["127.0.0.1/32", "10.0.0.0/8"]
    assert settings.ai_allowed_models == ["llama3.1", "qwen3-coder:latest"]


def test_models_cache_seconds_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        make_settings(models_cache_seconds=-1)


def test_empty_values_in_env_file_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("CLOUD_PROVIDER=\nCORS_ORIGINS=\n", encoding="utf-8")

    settings = Settings(
        _env_file=env_file,
        admin_username="admin",
        admin_password_hash=PASSWORD_HASH,
        jwt_secret="x" * 40,
    )

    assert settings.cloud_provider is None
    assert settings.cors_origins == ["http://localhost:5173"]


def test_log_level_is_normalized() -> None:
    assert make_settings(log_level="debug").log_level == "DEBUG"
    assert make_settings(log_level="warning").log_level == "WARNING"


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(log_level="VERBOSE")


def test_admin_password_hash_is_required() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_username="admin", jwt_secret="x" * 40)


def test_jwt_secret_is_required() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_username="admin", admin_password_hash=PASSWORD_HASH)


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
    settings = make_settings(
        environment="production", jwt_secret="a" * 48, key_encryption_secret="b" * 48
    )
    assert settings.is_production is True


def test_shutdown_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        make_settings(shutdown_timeout_seconds=0)


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


def test_deprecated_ai_provider_ollama_maps_to_default_model() -> None:
    settings = make_settings(ai_provider="ollama", ollama_model="qwen3")

    assert settings.ai_default_model == "ollama/qwen3"


def test_deprecated_ai_provider_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError, match="AI_PROVIDER deve ser"):
        make_settings(ai_provider="openai")


def test_deprecated_cloud_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError, match="CLOUD_PROVIDER desconhecido"):
        make_settings(ai_provider="cloud", cloud_provider="acme", cloud_model="modelo")


@pytest.mark.parametrize(
    ("provider", "attribute"),
    [
        ("anthropic", "anthropic_api_key_value"),
        ("google", "google_api_key_value"),
    ],
)
def test_deprecated_cloud_api_key_is_forwarded(provider: str, attribute: str) -> None:
    settings = make_settings(
        ai_provider="cloud",
        cloud_provider=provider,
        cloud_model="modelo",
        cloud_api_key="chave-legada",
    )

    assert getattr(settings, attribute) == "chave-legada"


def test_model_aliases_are_parsed_and_validated() -> None:
    assert make_settings(ai_model_aliases='{"fast": "ollama/llama3.1"}').ai_model_aliases == {
        "fast": "ollama/llama3.1"
    }
    assert make_settings(ai_model_aliases="   ").ai_model_aliases == {}

    for invalid in ("{nao-json", '{"fast": 1}', '{" ": "ollama/llama3.1"}'):
        with pytest.raises(ValidationError):
            make_settings(ai_model_aliases=invalid)


def test_default_model_shape_is_validated() -> None:
    for invalid in ("   ", "acme/modelo", "ollama/   "):
        with pytest.raises(ValidationError):
            make_settings(ai_default_model=invalid)


def test_unknown_enabled_provider_is_rejected() -> None:
    with pytest.raises(ValidationError, match="AI_ENABLED_PROVIDERS"):
        make_settings(ai_enabled_providers="ollama,acme")


def test_default_model_must_be_enabled() -> None:
    with pytest.raises(ValidationError, match="não está em AI_ENABLED_PROVIDERS"):
        make_settings(ai_default_model="ollama/llama3.1", ai_enabled_providers="openai")


def test_fallback_and_alias_providers_must_be_known() -> None:
    with pytest.raises(ValidationError, match="AI_FALLBACK_CHAIN"):
        make_settings(ai_fallback_chain="acme/modelo")
    with pytest.raises(ValidationError, match="AI_MODEL_ALIASES"):
        make_settings(ai_model_aliases='{"fast": "acme/modelo"}')


def test_database_url_and_backup_dir_are_validated() -> None:
    for invalid in ("postgres://host/db", "sqlite:///", "sqlite:///:memory:"):
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            make_settings(database_url=invalid)
    with pytest.raises(ValidationError, match="DB_BACKUP_DIR"):
        make_settings(db_backup_dir="   ")


def test_provider_urls_must_use_http() -> None:
    with pytest.raises(ValidationError, match="OPENAI_COMPAT_BASE_URL"):
        make_settings(openai_compat_base_url="ftp://exemplo")
    with pytest.raises(ValidationError, match="CLOUD_BASE_URL"):
        make_settings(cloud_base_url="exemplo.com")


def test_deprecated_api_key_accessors() -> None:
    settings = make_settings(openai_compat_api_key="compat", cloud_api_key="nuvem")

    assert settings.openai_compat_api_key_value == "compat"
    assert settings.cloud_api_key_value == "nuvem"
    assert settings.ollama_cloud_api_key_value is None


def test_compat_defaults() -> None:
    settings = make_settings()

    assert settings.compat_openai_enabled is True
    assert settings.compat_anthropic_enabled is True
    assert settings.compat_ollama_enabled is True
    assert settings.compat_api_keys == []
    assert settings.compat_api_key_rate_per_min == 60
    assert settings.compat_allow_anonymous is False
    assert settings.compat_ollama_allow_localhost is False


def test_compat_keys_are_split_and_trimmed() -> None:
    settings = make_settings(compat_api_keys=" nxa_live_a , nxa_test_b ,,")

    assert settings.compat_api_keys == ["nxa_live_a", "nxa_test_b"]


def test_compat_rate_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        make_settings(compat_api_key_rate_per_min=0)


def test_production_rejects_anonymous_compat() -> None:
    with pytest.raises(ValidationError, match="proibidos em produção"):
        make_settings(
            environment="production",
            jwt_secret="z" * 40,
            key_encryption_secret="b" * 48,
            compat_allow_anonymous=True,
        )
    with pytest.raises(ValidationError, match="proibidos em produção"):
        make_settings(
            environment="production",
            jwt_secret="z" * 40,
            key_encryption_secret="b" * 48,
            compat_ollama_allow_localhost=True,
        )


def test_production_allows_compat_with_api_keys() -> None:
    settings = make_settings(
        environment="production",
        jwt_secret="z" * 40,
        key_encryption_secret="b" * 48,
        compat_api_keys="nxa_live_ok",
    )

    assert settings.compat_api_keys == ["nxa_live_ok"]


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

"""Configuração da aplicação (12-factor, validação fail-fast)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Hash argon2 (argon2id/argon2i/argon2d) ou bcrypt.
_PASSWORD_HASH_PREFIX = re.compile(r"^\$(?:argon2(?:id|i|d)?|2[abxy])\$")

# Valores de exemplo que nunca podem ir para produção (RNF-SEG-12).
_WEAK_SECRETS = frozenset(
    {
        "changeme",
        "change-me",
        "secret",
        "secret-key",
        "dev-secret",
        "development-secret",
        "jwt-secret",
        "your-secret",
        "your-secret-key",
        "password",
        "troque-por-um-segredo-aleatorio-de-32-bytes-ou-mais",
    }
)

MIN_JWT_SECRET_BYTES = 32
DEFAULT_MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024


class Settings(BaseSettings):
    """Configuração carregada de variáveis de ambiente e, opcionalmente, de `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Ambiente -----------------------------------------------------------
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"

    # --- Administrador ------------------------------------------------------
    admin_username: str
    admin_password_hash: SecretStr
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    admin_token_expire_minutes: int = Field(default=60, gt=0)

    # --- Convidados ---------------------------------------------------------
    guest_token_expire_hours: int = Field(default=24, gt=0)
    guest_daily_messages: int = Field(default=10, ge=0)
    guest_rate_per_min: int = Field(default=3, ge=1)
    guest_max_messages_per_request: int = Field(default=20, ge=1)
    guest_max_message_chars: int = Field(default=2000, ge=1)
    guest_ip_daily_messages: int = Field(default=30, ge=0)
    guest_tokens_per_ip_per_hour: int = Field(default=10, ge=1)

    # --- Limites do administrador ------------------------------------------
    rate_limit_admin_chat_per_min: int = Field(default=60, ge=1)
    max_messages_per_request: int = Field(default=100, ge=1)
    max_message_chars: int = Field(default=8000, ge=1)
    allow_client_system_messages: bool = False
    default_system_prompt: str | None = None

    # --- Segurança / entrada ------------------------------------------------
    login_max_attempts: int = Field(default=5, ge=1)
    login_lock_minutes: int = Field(default=15, ge=1)
    max_request_body_bytes: int = Field(default=DEFAULT_MAX_REQUEST_BODY_BYTES, gt=0)
    shutdown_timeout_seconds: float = Field(default=30.0, gt=0)
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    uvicorn_workers: int = Field(default=1, ge=1)

    # --- Provedor de IA -----------------------------------------------------
    ai_provider: Literal["ollama", "cloud"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    cloud_provider: Literal["openai", "anthropic", "google"] | None = None
    cloud_base_url: str | None = None
    cloud_model: str | None = None
    cloud_api_key: SecretStr | None = None
    ai_timeout_seconds: float = Field(default=120.0, gt=0)
    ai_stream_idle_timeout_seconds: float = Field(default=60.0, gt=0)
    ai_max_output_tokens: int = Field(default=1024, gt=0)
    ai_max_output_seconds: float = Field(default=300.0, gt=0)
    ai_allowed_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    models_cache_seconds: float = Field(default=10.0, ge=0)

    # --- Redis (opcional) ---------------------------------------------------
    redis_url: str | None = None

    # --- Validadores de campo ----------------------------------------------
    @field_validator("admin_username")
    @classmethod
    def _validate_admin_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ADMIN_USERNAME não pode ser vazio")
        return value

    @field_validator("admin_password_hash")
    @classmethod
    def _validate_password_hash(cls, value: SecretStr) -> SecretStr:
        if not _PASSWORD_HASH_PREFIX.match(value.get_secret_value()):
            raise ValueError("ADMIN_PASSWORD_HASH deve ser um hash argon2 ou bcrypt")
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def _validate_jwt_algorithm(cls, value: str) -> str:
        # Apenas HMAC: o segredo é simétrico e o SRS adota HS256.
        if value not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM deve ser HS256, HS384 ou HS512")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL inválido")
        return value

    @field_validator("trusted_proxies", "cors_origins", "ai_allowed_models", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: Any) -> Any:
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",")]
            return [item for item in items if item]
        return value

    # --- Validações cruzadas ------------------------------------------------
    @model_validator(mode="after")
    def _cross_checks(self) -> Settings:
        self._check_secret()
        self._check_workers()
        self._check_body_limit()
        self._check_cloud_provider()
        self._check_urls()
        return self

    def _check_secret(self) -> None:
        secret = self.jwt_secret_value
        if self.environment != "production":
            return
        if len(secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"JWT_SECRET deve ter ao menos {MIN_JWT_SECRET_BYTES} bytes em produção"
            )
        if secret.strip().lower() in _WEAK_SECRETS:
            raise ValueError("JWT_SECRET usa um valor de exemplo/fraco em produção")

    def _check_workers(self) -> None:
        if self.uvicorn_workers > 1 and not self.redis_url:
            raise ValueError(
                "UVICORN_WORKERS > 1 exige REDIS_URL (contadores em memória não são compartilhados)"
            )

    def _check_body_limit(self) -> None:
        max_payload = self.max_messages_per_request * self.max_message_chars
        if self.max_request_body_bytes < max_payload:
            raise ValueError(
                "MAX_REQUEST_BODY_BYTES menor que o maior payload permitido ao admin "
                f"({max_payload} bytes)"
            )

    def _check_cloud_provider(self) -> None:
        if self.ai_provider != "cloud":
            return
        if not self.cloud_provider:
            raise ValueError("AI_PROVIDER=cloud exige CLOUD_PROVIDER")
        if not self.cloud_model:
            raise ValueError("AI_PROVIDER=cloud exige CLOUD_MODEL")

    def _check_urls(self) -> None:
        if not self.ollama_url.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_URL deve começar com http:// ou https://")

    # --- Acessores ----------------------------------------------------------
    @property
    def jwt_secret_value(self) -> str:
        return self.jwt_secret.get_secret_value()

    @property
    def admin_password_hash_value(self) -> str:
        return self.admin_password_hash.get_secret_value()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuração do processo (carregada uma única vez)."""
    # mypy não enxerga que o BaseSettings preenche os campos obrigatórios via ambiente.
    return Settings()

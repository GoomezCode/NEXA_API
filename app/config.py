"""Configuração da aplicação (12-factor, validação fail-fast)."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
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
DEFAULT_AI_DEFAULT_MODEL = "ollama/llama3.1"
DEFAULT_DATABASE_URL = "sqlite:///./data/nexa.db"

#: Prefixo aceito para o SQLite (RF-USR-01, RD-01).
SQLITE_URL_PREFIX = "sqlite:///"

#: Provedores conhecidos pelo registry (RF-GTW-01).
KNOWN_PROVIDERS = frozenset(
    {
        "ollama",
        "ollama_cloud",
        "openai",
        "anthropic",
        "google",
        "openai_compatible",
        "fake",
    }
)

_logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Configuração carregada de variáveis de ambiente e, opcionalmente, de `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
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

    # --- Gateway de IA (v2) -------------------------------------------------
    ai_default_model: str = DEFAULT_AI_DEFAULT_MODEL
    ai_model_aliases: dict[str, str] = Field(default_factory=dict)
    ai_fallback_chain: Annotated[list[str], NoDecode] = Field(default_factory=list)
    ai_enabled_providers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    ai_max_retries: int = Field(default=1, ge=0)
    ai_timeout_seconds: float = Field(default=120.0, gt=0)
    ai_stream_idle_timeout_seconds: float = Field(default=60.0, gt=0)
    ai_max_output_tokens: int = Field(default=1024, gt=0)
    ai_max_output_seconds: float = Field(default=300.0, gt=0)
    ai_allowed_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    models_cache_seconds: float = Field(default=10.0, ge=0)

    # --- Ollama (local e cloud) --------------------------------------------
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ollama_api_key: SecretStr | None = None
    ollama_cloud_url: str = "https://ollama.com"
    ollama_cloud_api_key: SecretStr | None = None
    ollama_cloud_default_model: str = "qwen3-coder:480b"

    # --- OpenAI -------------------------------------------------------------
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = "gpt-4o-mini"
    openai_org_id: str | None = None

    # --- Anthropic ----------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"
    anthropic_default_model: str = "claude-sonnet-4-5"

    # --- Google Gemini ------------------------------------------------------
    google_api_key: SecretStr | None = None
    google_base_url: str = "https://generativelanguage.googleapis.com"
    google_default_model: str = "gemini-2.5-flash"

    # --- OpenAI-compatible --------------------------------------------------
    openai_compat_name: str = "groq"
    openai_compat_base_url: str | None = None
    openai_compat_api_key: SecretStr | None = None
    openai_compat_default_model: str | None = None

    # --- Descontinuados na v2.0 (convertidos no startup) --------------------
    ai_provider: str | None = None
    cloud_provider: str | None = None
    cloud_base_url: str | None = None
    cloud_model: str | None = None
    cloud_api_key: SecretStr | None = None

    # --- Redis (opcional) ---------------------------------------------------
    redis_url: str | None = None

    # --- SQLite de usuários (v2.0) -----------------------------------------
    database_url: str = DEFAULT_DATABASE_URL
    sqlite_wal: bool = True
    sqlite_synchronous: Literal["NORMAL", "FULL"] = "NORMAL"
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=0)
    sqlite_cache_size_kb: int = Field(default=65536, ge=0)
    sqlite_mmap_bytes: int = Field(default=268435456, ge=0)
    sqlite_read_pool_size: int = Field(default=4, ge=1, le=32)
    users_cache_ttl_seconds: float = Field(default=30.0, ge=0)
    users_cache_max_entries: int = Field(default=10000, ge=1)
    allow_test_users: bool = True
    test_user_default_daily_messages: int = Field(default=100, ge=0)
    test_user_default_rate_per_min: int = Field(default=10, ge=1)
    test_user_ttl_hours: int = Field(default=168, ge=0)
    test_user_max_bulk: int = Field(default=100, ge=1, le=1000)
    db_backup_before_migration: bool = True
    db_backup_dir: str = "./data/backups"
    db_startup_check: Literal["quick", "full", "off"] = "quick"
    db_optimize_on_shutdown: bool = True

    # --- Compatibilidade com APIs externas (v2.0) ---------------------------
    compat_openai_enabled: bool = True
    compat_anthropic_enabled: bool = True
    compat_ollama_enabled: bool = True
    compat_api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    compat_api_key_rate_per_min: int = Field(default=60, ge=1)
    compat_allow_anonymous: bool = False
    compat_ollama_allow_localhost: bool = False
    compat_anthropic_version: str = "2023-06-01"

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

    @field_validator(
        "trusted_proxies",
        "cors_origins",
        "ai_allowed_models",
        "ai_fallback_chain",
        "ai_enabled_providers",
        "compat_api_keys",
        mode="before",
    )
    @classmethod
    def _split_comma_separated(cls, value: Any) -> Any:
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",")]
            return [item for item in items if item]
        return value

    @field_validator("ai_model_aliases", mode="before")
    @classmethod
    def _parse_model_aliases(cls, value: Any) -> Any:
        """`AI_MODEL_ALIASES` é JSON: `{"fast": "ollama/llama3.1"}`."""
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                value = json.loads(raw)
            except ValueError as exc:
                raise ValueError("AI_MODEL_ALIASES deve ser um objeto JSON") from exc
        if isinstance(value, dict):
            aliases: dict[str, str] = {}
            for alias, model_id in value.items():
                if not isinstance(alias, str) or not isinstance(model_id, str):
                    raise ValueError("AI_MODEL_ALIASES deve mapear strings para strings")
                alias = alias.strip()
                model_id = model_id.strip()
                if not alias or not model_id:
                    raise ValueError("AI_MODEL_ALIASES não aceita alias/modelo vazio")
                aliases[alias] = model_id
            return aliases
        return value

    @field_validator("ai_default_model")
    @classmethod
    def _validate_default_model_shape(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("AI_DEFAULT_MODEL não pode ser vazio")
        if "/" in value:
            provider, _, model = value.partition("/")
            if provider not in KNOWN_PROVIDERS:
                raise ValueError(f"AI_DEFAULT_MODEL usa provedor desconhecido: {provider}")
            if not model.strip():
                raise ValueError("AI_DEFAULT_MODEL deve incluir o modelo após 'provider/'")
        return value

    @field_validator("ai_enabled_providers")
    @classmethod
    def _validate_enabled_providers(cls, value: list[str]) -> list[str]:
        for name in value:
            if name not in KNOWN_PROVIDERS:
                raise ValueError(f"AI_ENABLED_PROVIDERS contém provedor desconhecido: {name}")
        return value

    # --- Validações cruzadas ------------------------------------------------
    @model_validator(mode="after")
    def _cross_checks(self) -> Settings:
        self._apply_deprecations()
        self._normalize_ai_default_model()
        self._check_secret()
        self._check_workers()
        self._check_body_limit()
        self._check_ai()
        self._check_database()
        self._check_compat()
        self._check_urls()
        return self

    def _apply_deprecations(self) -> None:
        """Converte `AI_PROVIDER`/`CLOUD_*` da v1.1 para `AI_DEFAULT_MODEL`."""
        if self.ai_provider is None:
            return

        _logger.warning(
            "config_deprecated_ai_provider",
            extra={"hint": "AI_PROVIDER/CLOUD_* foram descontinuados; use AI_DEFAULT_MODEL."},
        )
        if self.ai_provider == "ollama":
            if self.ai_default_model == DEFAULT_AI_DEFAULT_MODEL:
                self.ai_default_model = f"ollama/{self.ollama_model}"
            return

        if self.ai_provider != "cloud":
            raise ValueError("AI_PROVIDER deve ser 'ollama' ou 'cloud' (descontinuado)")
        if not self.cloud_provider:
            raise ValueError("AI_PROVIDER=cloud exige CLOUD_PROVIDER (descontinuado)")
        if self.cloud_provider not in KNOWN_PROVIDERS:
            raise ValueError(f"CLOUD_PROVIDER desconhecido: {self.cloud_provider}")
        if not self.cloud_model:
            raise ValueError("AI_PROVIDER=cloud exige CLOUD_MODEL (descontinuado)")

        self.ai_default_model = f"{self.cloud_provider}/{self.cloud_model}"
        if self.cloud_api_key is not None:
            if self.cloud_provider == "openai" and self.openai_api_key is None:
                self.openai_api_key = self.cloud_api_key
            elif self.cloud_provider == "anthropic" and self.anthropic_api_key is None:
                self.anthropic_api_key = self.cloud_api_key
            elif self.cloud_provider == "google" and self.google_api_key is None:
                self.google_api_key = self.cloud_api_key

    def _normalize_ai_default_model(self) -> None:
        value = self.ai_default_model
        value = self.ai_model_aliases.get(value, value)
        if "/" not in value:
            value = f"ollama/{value}"
        provider, _, model = value.partition("/")
        if provider not in KNOWN_PROVIDERS:
            raise ValueError(f"AI_DEFAULT_MODEL usa provedor desconhecido: {provider}")
        if not model.strip():
            raise ValueError("AI_DEFAULT_MODEL deve incluir o modelo após 'provider/'")
        self.ai_default_model = value

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

    def _check_ai(self) -> None:
        default_provider = self.ai_default_model.partition("/")[0]
        if self.ai_enabled_providers and default_provider not in self.ai_enabled_providers:
            raise ValueError(
                f"AI_DEFAULT_MODEL usa '{default_provider}', que não está em AI_ENABLED_PROVIDERS"
            )

        for entry in self.ai_fallback_chain:
            expanded = self.ai_model_aliases.get(entry, entry)
            if "/" in expanded:
                provider = expanded.partition("/")[0]
                if provider not in KNOWN_PROVIDERS:
                    raise ValueError(f"AI_FALLBACK_CHAIN contém provedor desconhecido: {provider}")

        for alias, model_id in self.ai_model_aliases.items():
            if "/" in model_id:
                provider = model_id.partition("/")[0]
                if provider not in KNOWN_PROVIDERS:
                    raise ValueError(
                        f"AI_MODEL_ALIASES['{alias}'] usa provedor desconhecido: {provider}"
                    )

    def _check_compat(self) -> None:
        """Compatibilidade sem chave é exclusiva de desenvolvimento (RF-CMP-05)."""
        if self.environment == "production" and (
            self.compat_allow_anonymous or self.compat_ollama_allow_localhost
        ):
            raise ValueError(
                "COMPAT_ALLOW_ANONYMOUS/COMPAT_OLLAMA_ALLOW_LOCALHOST são proibidos em produção"
            )

    def _check_urls(self) -> None:
        urls = {
            "OLLAMA_URL": self.ollama_url,
            "OLLAMA_CLOUD_URL": self.ollama_cloud_url,
            "OPENAI_BASE_URL": self.openai_base_url,
            "ANTHROPIC_BASE_URL": self.anthropic_base_url,
            "GOOGLE_BASE_URL": self.google_base_url,
        }
        if self.openai_compat_base_url:
            urls["OPENAI_COMPAT_BASE_URL"] = self.openai_compat_base_url
        if self.cloud_base_url:
            urls["CLOUD_BASE_URL"] = self.cloud_base_url

        for name, url in urls.items():
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"{name} deve começar com http:// ou https://")

    def _check_database(self) -> None:
        value = self.database_url.strip()
        if not value.startswith(SQLITE_URL_PREFIX):
            raise ValueError("DATABASE_URL deve começar com sqlite:///")
        path = value[len(SQLITE_URL_PREFIX) :].strip()
        if not path:
            raise ValueError("DATABASE_URL deve incluir o caminho do arquivo SQLite")
        if path == ":memory:":
            raise ValueError("DATABASE_URL não aceita banco em memória (use um arquivo)")
        if not self.db_backup_dir.strip():
            raise ValueError("DB_BACKUP_DIR não pode ser vazio")
        self.database_url = f"{SQLITE_URL_PREFIX}{path}"

    # --- Acessores ----------------------------------------------------------
    @property
    def database_path(self) -> Path:
        """Caminho do arquivo SQLite derivado de `DATABASE_URL`."""
        return Path(self.database_url[len(SQLITE_URL_PREFIX) :]).expanduser()

    @property
    def jwt_secret_value(self) -> str:
        return self.jwt_secret.get_secret_value()

    @property
    def admin_password_hash_value(self) -> str:
        return self.admin_password_hash.get_secret_value()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def ollama_api_key_value(self) -> str | None:
        return self._secret(self.ollama_api_key)

    @property
    def ollama_cloud_api_key_value(self) -> str | None:
        return self._secret(self.ollama_cloud_api_key)

    @property
    def openai_api_key_value(self) -> str | None:
        return self._secret(self.openai_api_key)

    @property
    def anthropic_api_key_value(self) -> str | None:
        return self._secret(self.anthropic_api_key)

    @property
    def google_api_key_value(self) -> str | None:
        return self._secret(self.google_api_key)

    @property
    def openai_compat_api_key_value(self) -> str | None:
        return self._secret(self.openai_compat_api_key)

    @property
    def cloud_api_key_value(self) -> str | None:
        return self._secret(self.cloud_api_key)

    @staticmethod
    def _secret(value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value is not None else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuração do processo (carregada uma única vez)."""
    # mypy não enxerga que o BaseSettings preenche os campos obrigatórios via ambiente.
    return Settings()


__all__ = [
    "DEFAULT_AI_DEFAULT_MODEL",
    "DEFAULT_DATABASE_URL",
    "KNOWN_PROVIDERS",
    "SQLITE_URL_PREFIX",
    "Settings",
    "get_settings",
]

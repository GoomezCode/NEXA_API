"""Fábrica da aplicação FastAPI (sem instância global)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.ai import AIProvider, build_provider
from app.api import auth, chat, system, usage
from app.config import Settings, get_settings
from app.core.auth import AuthService
from app.core.clock import Clock, SystemClock
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodyLimitMiddleware, RequestContextMiddleware
from app.core.rate_limit import SlidingWindowLimiter
from app.core.usage_store import InMemoryUsageStore

EXPOSED_HEADERS = [
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "Retry-After",
    "X-Request-ID",
    "Content-Type",
]

ALLOWED_HEADERS = ["Authorization", "Content-Type", "X-Request-ID"]
ALLOWED_METHODS = ["GET", "POST", "OPTIONS"]

DESCRIPTION = """
API do chat de IA via web, conforme o SRS v1.1.

- Autenticação por JWT (`admin` e `guest`).
- Chat com resposta JSON ou streaming SSE.
- Cotas em memória por convidado e por IP.
- Erros no padrão Problem Details (`application/problem+json`).
"""


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    provider: AIProvider | None = None,
) -> FastAPI:
    """Cria a aplicação com configuração explícita ou vinda do ambiente.

    `provider` permite injetar qualquer implementação de `AIProvider` (testes e
    CA-15); sem ele, a factory escolhe pelo `AI_PROVIDER` configurado.
    """
    resolved = settings or get_settings()
    resolved_clock = clock or SystemClock()
    resolved_provider = provider or build_provider(resolved)
    usage_store = InMemoryUsageStore(resolved, resolved_clock)
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await usage_store.start()
        try:
            yield
        finally:
            await usage_store.aclose()

    app = FastAPI(
        title="Nexa API",
        description=DESCRIPTION,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Estado compartilhado. O chat (F4/F5) consome apenas o contrato `AIProvider`.
    app.state.settings = resolved
    app.state.clock = resolved_clock
    app.state.ai_provider = resolved_provider
    app.state.ai_provider_name = resolved_provider.name
    app.state.ai_model = resolved_provider.model
    app.state.ai_health = resolved_provider.health
    app.state.usage_store = usage_store
    app.state.auth_service = AuthService(resolved, resolved_clock)
    app.state.login_limiter = SlidingWindowLimiter(
        resolved_clock,
        limit=resolved.login_max_attempts,
        window_seconds=resolved.login_lock_minutes * 60,
    )
    app.state.guest_token_limiter = SlidingWindowLimiter(
        resolved_clock,
        limit=resolved.guest_tokens_per_ip_per_hour,
        window_seconds=3600,
    )

    # Pilha (de dentro para fora): BodyLimit → CORS → RequestContext.
    # RequestContext é o mais externo para que *toda* resposta (inclusive
    # preflight) receba X-Request-ID e seja registrada em log (RF-SIS-03).
    app.add_middleware(BodyLimitMiddleware, max_bytes=resolved.max_request_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=False,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        expose_headers=EXPOSED_HEADERS,
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware, settings=resolved)

    register_exception_handlers(app)
    # Em `POST /chat`, o 422 também carrega os cabeçalhos de cota (RF-USO-08).
    app.add_exception_handler(RequestValidationError, chat.validation_error_with_quota)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(usage.router)
    app.include_router(chat.router)
    return app


__all__ = ["create_app"]

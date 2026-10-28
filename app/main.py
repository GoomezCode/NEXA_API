"""Fábrica da aplicação FastAPI (sem instância global)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import system
from app.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodyLimitMiddleware, RequestContextMiddleware

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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Cria a aplicação com configuração explícita ou vinda do ambiente."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="Nexa API",
        description=DESCRIPTION,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Estado compartilhado. O check de IA é registrado na fase de provedores.
    app.state.settings = resolved
    app.state.ai_health = None
    app.state.ai_provider_name = resolved.ai_provider

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
    app.include_router(system.router)
    return app


__all__ = ["create_app"]

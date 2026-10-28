"""Entrada de produção: uvicorn com shutdown gracioso configurável (RNF-CON-04).

O prazo é lido de `SHUTDOWN_TIMEOUT_SECONDS` e repassado ao uvicorn, que para
de aceitar conexões e aguarda as requisições em andamento (inclusive streams)
até o limite antes de encerrar.
"""

from __future__ import annotations

import uvicorn

from app.config import Settings, get_settings

HOST = "0.0.0.0"
PORT = 8000


def build_config(settings: Settings | None = None) -> uvicorn.Config:
    """Configuração do servidor; separada da execução para permitir testes."""
    resolved = settings or get_settings()
    return uvicorn.Config(
        "app.main:create_app",
        factory=True,
        host=HOST,
        port=PORT,
        workers=resolved.uvicorn_workers,
        timeout_graceful_shutdown=int(resolved.shutdown_timeout_seconds),
        log_config=None,
    )


def main() -> None:  # pragma: no cover - entrada do processo
    uvicorn.Server(build_config()).run()


if __name__ == "__main__":  # pragma: no cover
    main()

"""Testes da entrada de produção (RNF-CON-04)."""

from __future__ import annotations

from app.__main__ import build_config
from tests.conftest import make_settings


def test_build_config_uses_shutdown_deadline_and_single_process() -> None:
    config = build_config(make_settings(shutdown_timeout_seconds=12.5))

    assert config.factory is True
    assert config.host == "0.0.0.0"
    assert config.port == 8000
    assert config.workers == 1
    assert config.timeout_graceful_shutdown == 12


def test_build_config_keeps_configured_workers() -> None:
    settings = make_settings(uvicorn_workers=2, redis_url="redis://localhost:6379/0")

    config = build_config(settings)

    assert config.workers == 2

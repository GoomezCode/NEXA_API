"""Fixtures compartilhadas dos testes."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# Hash argon2id da senha "admin" (apenas para testes).
PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$KMwCxpCk5TYd4cdcRrnSFA$"
    "QKtV/j4myVFG6IfaM7tR6yZnx4KqRu4Q+PtQA958h48"
)

BASE_SETTINGS: dict[str, object] = {
    "_env_file": None,
    "environment": "development",
    "admin_username": "admin",
    "admin_password_hash": PASSWORD_HASH,
    "jwt_secret": "x" * 40,
}


def make_settings(**overrides: object) -> Settings:
    """Settings isolado do ambiente e do `.env`."""
    return Settings(**{**BASE_SETTINGS, **overrides})


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client

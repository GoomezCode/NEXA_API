"""API keys de compatibilidade: parsing, tempo constante e escopos (RF-AUT-11/13)."""

from __future__ import annotations

import hmac

import pytest

from app.core.api_keys import (
    DEFAULT_SCOPES,
    SCOPE_CHAT,
    SCOPE_MODELS,
    ApiKeyRegistry,
)
from tests.conftest import make_settings

KEY_A = "nxa_live_abcdefghijklmnop"
KEY_B = "nxa_test_0123456789"


def test_registry_reads_and_deduplicates_keys() -> None:
    registry = ApiKeyRegistry([f" {KEY_A} ", KEY_A, "", KEY_B])

    assert registry.enabled is True
    assert registry.count == 2


def test_authenticate_matches_only_the_exact_key() -> None:
    registry = ApiKeyRegistry([KEY_A, KEY_B])

    identity = registry.authenticate(KEY_A)

    assert identity is not None
    assert identity.prefix == KEY_A[:8]
    assert len(identity.fingerprint) == 12
    assert identity.fingerprint != KEY_B[:12]
    assert identity.scopes == DEFAULT_SCOPES
    assert identity.allows(SCOPE_CHAT)
    assert identity.allows(SCOPE_MODELS)
    assert identity.subject == f"apikey:{identity.fingerprint}"
    assert registry.authenticate(KEY_A + "x") is None
    assert registry.authenticate("nxa_inexistente") is None


def test_empty_registry_rejects_everything() -> None:
    registry = ApiKeyRegistry([])

    assert registry.enabled is False
    assert registry.count == 0
    assert registry.authenticate("nxa_qualquer") is None


def test_all_keys_are_compared_even_after_a_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ApiKeyRegistry([KEY_A, KEY_B, "nxa_third_key_000"])
    calls: list[str] = []
    original = hmac.compare_digest

    def spy(left: str, right: str) -> bool:
        calls.append(right)
        return original(left, right)

    monkeypatch.setattr(hmac, "compare_digest", spy)

    assert registry.authenticate(KEY_A) is not None
    assert len(calls) == 3


def test_registry_is_built_from_settings() -> None:
    settings = make_settings(compat_api_keys=f"{KEY_A}, {KEY_B}")

    registry = ApiKeyRegistry.from_settings(settings)

    assert registry.count == 2
    assert registry.authenticate(KEY_B) is not None

"""Testes do catálogo de modelos e da regra de seleção (RF-CHAT-13/F11)."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.base import ModelInfo, ProviderTimeout, ProviderUnavailable
from app.ai.fake import FakeProvider, fake_models
from app.core.models import ModelCatalog, is_model_selectable
from tests.conftest import FakeClock


def make_catalog(
    provider: FakeProvider | None = None,
    clock: FakeClock | None = None,
    *,
    ttl_seconds: float = 10.0,
) -> tuple[ModelCatalog, FakeProvider, FakeClock]:
    fake = provider or FakeProvider()
    fake_clock = clock or FakeClock()
    return ModelCatalog(fake, fake_clock, ttl_seconds=ttl_seconds), fake, fake_clock


async def test_list_models_returns_provider_catalog() -> None:
    catalog, provider, _ = make_catalog()

    models = await catalog.list_models(request_id="req-1")

    assert models == list(fake_models())
    assert provider.models_calls == ["req-1"]


async def test_list_models_reuses_cache_within_ttl() -> None:
    catalog, provider, clock = make_catalog()

    first = await catalog.list_models()
    clock.advance(9)
    second = await catalog.list_models()

    assert first == second
    assert len(provider.models_calls) == 1


async def test_list_models_refreshes_after_ttl() -> None:
    catalog, provider, clock = make_catalog()

    await catalog.list_models()
    clock.advance(11)
    await catalog.list_models()

    assert len(provider.models_calls) == 2


async def test_list_models_refresh_ignores_cache() -> None:
    catalog, provider, _ = make_catalog()

    await catalog.list_models()
    await catalog.list_models(refresh=True)

    assert len(provider.models_calls) == 2


async def test_cache_is_disabled_with_zero_ttl() -> None:
    catalog, provider, _ = make_catalog(ttl_seconds=0)

    await catalog.list_models()
    await catalog.list_models()

    assert len(provider.models_calls) == 2


async def test_parallel_calls_load_the_catalog_once() -> None:
    catalog, provider, _ = make_catalog()

    results = await asyncio.gather(*(catalog.list_models() for _ in range(5)))

    assert all(result == results[0] for result in results)
    assert len(provider.models_calls) == 1


async def test_waiter_uses_the_cache_filled_by_the_first_call() -> None:
    gate = asyncio.Event()

    class GatedProvider(FakeProvider):
        async def list_models(self, *, request_id: str | None = None):
            self.models_calls.append(request_id)
            await gate.wait()
            return list(self.models)

    provider = GatedProvider()
    catalog = ModelCatalog(provider, FakeClock(), ttl_seconds=10)

    first = asyncio.create_task(catalog.list_models())
    await asyncio.sleep(0)
    second = asyncio.create_task(catalog.list_models())
    await asyncio.sleep(0)
    gate.set()

    results = await asyncio.gather(first, second)

    assert results[0] == results[1]
    assert len(provider.models_calls) == 1


async def test_provider_errors_propagate() -> None:
    catalog, provider, _ = make_catalog()
    provider.models_error = ProviderUnavailable("fora do ar")

    with pytest.raises(ProviderUnavailable):
        await catalog.list_models()


async def test_failed_lookup_is_not_cached() -> None:
    catalog, provider, _ = make_catalog()
    provider.models_error = ProviderTimeout("lento")

    with pytest.raises(ProviderTimeout):
        await catalog.list_models()

    provider.models_error = None
    models = await catalog.list_models()

    assert models == list(fake_models())
    assert len(provider.models_calls) == 2


async def test_resolve_accepts_exact_name() -> None:
    catalog, _, _ = make_catalog()

    assert await catalog.resolve("fake-small:latest") == "fake-small:latest"


async def test_resolve_completes_missing_latest_tag() -> None:
    catalog, _, _ = make_catalog()

    assert await catalog.resolve("fake-small") == "fake-small:latest"
    assert await catalog.resolve("fake-model") == "fake-model:latest"


async def test_resolve_keeps_unknown_tag() -> None:
    catalog, _, _ = make_catalog()

    assert await catalog.resolve("fake-model:v2") is None


async def test_resolve_returns_none_for_unknown_model() -> None:
    catalog, _, _ = make_catalog()

    assert await catalog.resolve("nao-existe") is None


def test_selectable_without_allowlist() -> None:
    assert is_model_selectable("qualquer:latest", allowed=[], default_model="fake-model")


def test_selectable_uses_allowlist() -> None:
    allowed = ["fake-small"]

    assert is_model_selectable("fake-small:latest", allowed=allowed, default_model="fake-model")
    assert not is_model_selectable("outro:latest", allowed=allowed, default_model="fake-model")


def test_default_model_is_always_selectable() -> None:
    allowed = ["fake-small"]

    assert is_model_selectable("fake-model:latest", allowed=allowed, default_model="fake-model")


def test_selectable_compares_names_with_and_without_tag() -> None:
    assert is_model_selectable(
        "fake-small", allowed=["fake-small:latest"], default_model="fake-model"
    )
    assert is_model_selectable(
        "fake-small:latest", allowed=["fake-small"], default_model="fake-model"
    )


def test_model_info_has_no_required_metadata() -> None:
    info = ModelInfo(name="apenas-nome")

    assert info.size_bytes is None
    assert info.modified_at is None

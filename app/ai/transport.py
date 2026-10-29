"""Transporte HTTP compartilhado pelos adapters de IA (RP-04).

Concentra timeouts, normalização de falhas de rede e leitura de SSE/NDJSON,
para que cada adapter cuide apenas do seu protocolo.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import nullcontext
from typing import Any

import httpx

from app.ai.base import ProviderError, ProviderTimeout, ProviderUnavailable

CONNECT_TIMEOUT_SECONDS = 10.0
HEALTH_TIMEOUT_SECONDS = 5.0


def timeout_scope(seconds: float | None) -> Any:
    """`asyncio.timeout` opcional, sem duplicar o `if` em cada chamada."""
    return nullcontext() if seconds is None else asyncio.timeout(seconds)


def request_headers(base: dict[str, str] | None, request_id: str | None) -> dict[str, str]:
    """Cabeçalhos do adapter, com propagação de `X-Request-ID` quando possível."""
    headers = dict(base or {})
    if request_id:
        headers.setdefault("X-Request-ID", request_id)
    return headers


def _timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=min(CONNECT_TIMEOUT_SECONDS, seconds))


async def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float,
    max_seconds: float | None = None,
    label: str = "provedor",
) -> Any:
    """`POST` com JSON; falhas de rede viram erros normalizados do contrato.

    `TimeoutError` (teto de geração) é propagado para o adapter traduzir em
    `finish_reason = timeout`.
    """
    try:
        async with timeout_scope(max_seconds):
            async with httpx.AsyncClient(timeout=_timeout(timeout)) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()
    except httpx.TimeoutException as exc:
        raise ProviderTimeout(f"{label} não respondeu a tempo.") from exc
    except httpx.HTTPStatusError as exc:
        raise ProviderError(f"{label} retornou HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"Não foi possível conectar ao {label}.") from exc
    except ValueError as exc:
        raise ProviderError(f"Resposta inválida do {label}.") from exc


async def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float,
    label: str = "provedor",
) -> Any:
    """`GET` com JSON; mesmas normalizações de `post_json`."""
    try:
        async with httpx.AsyncClient(timeout=_timeout(timeout)) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise ProviderTimeout(f"{label} não respondeu a tempo.") from exc
    except httpx.HTTPStatusError as exc:
        raise ProviderError(f"{label} retornou HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"Não foi possível conectar ao {label}.") from exc
    except ValueError as exc:
        raise ProviderError(f"Resposta inválida do {label}.") from exc


async def is_available(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = HEALTH_TIMEOUT_SECONDS,
) -> bool:
    """Health check leve: `True` apenas para `2xx`."""
    try:
        async with timeout_scope(timeout):
            async with httpx.AsyncClient(timeout=_timeout(timeout)) as client:
                response = await client.get(url, headers=headers)
                return response.status_code == 200
    except (TimeoutError, httpx.HTTPError, ValueError):
        return False


async def iter_sse_data(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    idle_timeout: float,
    max_seconds: float,
    label: str = "provedor",
) -> AsyncIterator[str]:
    """Itera os payloads `data:` de um stream SSE.

    `TimeoutError` do teto de geração é propagado; o adapter o converte em
    `finish_reason = timeout` (RF-CHAT-12).
    """
    try:
        async with (
            timeout_scope(max_seconds),
            httpx.AsyncClient(timeout=_timeout(idle_timeout)) as client,
            client.stream("POST", url, json=payload, headers=headers) as response,
        ):
            if response.status_code >= 400:
                await response.aread()
                raise ProviderError(f"{label} retornou HTTP {response.status_code}.")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data:
                    yield data
    except httpx.TimeoutException as exc:
        raise ProviderTimeout(f"{label} não respondeu a tempo.") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"Não foi possível conectar ao {label}.") from exc


async def iter_ndjson_lines(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    idle_timeout: float,
    max_seconds: float,
    label: str = "provedor",
) -> AsyncIterator[str]:
    """Itera linhas não vazias de um stream NDJSON."""
    try:
        async with (
            timeout_scope(max_seconds),
            httpx.AsyncClient(timeout=_timeout(idle_timeout)) as client,
            client.stream("POST", url, json=payload, headers=headers) as response,
        ):
            if response.status_code >= 400:
                await response.aread()
                raise ProviderError(f"{label} retornou HTTP {response.status_code}.")
            async for line in response.aiter_lines():
                if line.strip():
                    yield line
    except httpx.TimeoutException as exc:
        raise ProviderTimeout(f"{label} não respondeu a tempo.") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"Não foi possível conectar ao {label}.") from exc


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "HEALTH_TIMEOUT_SECONDS",
    "get_json",
    "is_available",
    "iter_ndjson_lines",
    "iter_sse_data",
    "post_json",
    "request_headers",
    "timeout_scope",
]

"""Roteamento multi-provedor do gateway (RF-GTW-02/05/06/07/09).

O `ProviderRouter` resolve Model IDs (`provider/model`), aplica aliases e
allowlist, valida capacidades e concentra o fallback antes do primeiro token.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase

from app.ai.base import (
    AIProvider,
    Capabilities,
    ChatMessage,
    GatewayError,
    GenerationChunk,
    GenerationResult,
    ModelInfo,
    ModelNotAllowed,
    ModelNotFound,
    ProviderError,
    ProviderUnavailable,
    ToolSpec,
    provider_error_code,
)
from app.ai.capabilities import ensure_supported, merge_capabilities
from app.ai.factory import provider_label
from app.core.clock import Clock
from app.core.models import ModelCatalog


def _strip_latest(value: str) -> str:
    """Remove o sufixo `:latest` para comparar Model IDs canônicos."""
    return value[: -len(":latest")] if value.endswith(":latest") else value


def canonical_model_id(provider: str, model: str) -> str:
    """Model ID canônico `provider/model`, sem `:latest`."""
    return f"{provider}/{_strip_latest(model)}"


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """Modelo pronto para uso: adapter, nome no provedor e capacidades."""

    provider: AIProvider
    provider_name: str
    provider_label: str
    model_id: str
    model: str
    capabilities: Capabilities
    context_window: int | None = None


@dataclass(frozen=True, slots=True)
class RoutedResult:
    """Resultado completo e o provedor que efetivamente respondeu."""

    resolution: ResolvedModel
    result: GenerationResult


@dataclass(frozen=True, slots=True)
class RoutedChunk:
    """Trecho de stream com o provedor que o produziu."""

    resolution: ResolvedModel
    chunk: GenerationChunk


@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    """Catálogo de um provedor; falhas não derrubam os demais."""

    provider: str
    label: str
    available: bool
    default_model: str
    models: tuple[ModelInfo, ...]
    capabilities: Capabilities
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Visão agregada de todos os provedores habilitados."""

    default_model_id: str
    providers: tuple[ProviderCatalogEntry, ...]


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Resultado do health check de um provedor."""

    provider: str
    label: str
    default_model: str
    available: bool
    checked_at: datetime
    error: str | None = None


@dataclass(slots=True)
class _ProviderState:
    name: str
    label: str
    provider: AIProvider
    catalog: ModelCatalog


class ProviderRouter:
    """Resolve modelos, agrega catálogos e aplica fallback."""

    def __init__(
        self,
        providers: Mapping[str, AIProvider],
        *,
        default_model: str,
        clock: Clock,
        aliases: Mapping[str, str] | None = None,
        allowed_models: Sequence[str] = (),
        fallback_chain: Sequence[str] = (),
        max_retries: int = 1,
        cache_seconds: float = 10.0,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        if not providers:
            raise ProviderUnavailable("Nenhum provedor de IA habilitado.")

        self._aliases = dict(aliases or {})
        self._allowed = tuple(allowed_models)
        self._fallback_chain = tuple(fallback_chain)
        self._max_retries = max(0, max_retries)
        self._clock = clock

        self._providers: dict[str, _ProviderState] = {}
        for name, provider in providers.items():
            label = (labels or {}).get(name) or provider_label(name)
            self._providers[name] = _ProviderState(
                name=name,
                label=label,
                provider=provider,
                catalog=ModelCatalog(provider, clock, ttl_seconds=cache_seconds),
            )

        normalized_default = self._normalize_model_id(default_model)
        provider_name, model_name = self._split(normalized_default)
        if provider_name not in self._providers:
            raise ProviderUnavailable(
                f"Provedor padrão '{provider_name}' não está habilitado. "
                f"Disponíveis: {', '.join(sorted(self._providers))}."
            )
        self._default_provider_name = provider_name
        self._default_model_name = model_name

    # --- propriedades -------------------------------------------------------
    @property
    def providers(self) -> dict[str, AIProvider]:
        return {name: state.provider for name, state in self._providers.items()}

    @property
    def default_provider(self) -> AIProvider:
        return self._providers[self._default_provider_name].provider

    @property
    def default_provider_name(self) -> str:
        return self._default_provider_name

    @property
    def default_model_name(self) -> str:
        return self._default_model_name

    @property
    def default_model_id(self) -> str:
        return canonical_model_id(self._default_provider_name, self._default_model_name)

    @property
    def allowed_models(self) -> tuple[str, ...]:
        return self._allowed

    # --- resolução ----------------------------------------------------------
    async def resolve(
        self,
        requested: str | None,
        *,
        stream: bool,
        tools: Sequence[ToolSpec] | None = None,
        has_images: bool = False,
        json_mode: bool = False,
        request_id: str | None = None,
        enforce_allowlist: bool = True,
    ) -> ResolvedModel:
        """Resolve o Model ID e valida allowlist/capacidades antes da cota."""
        if requested is None:
            provider_name = self._default_provider_name
            model_name = self._default_model_name
            requested_id = self.default_model_id
        else:
            requested_id = self._normalize_model_id(requested)
            provider_name, model_name = self._split(requested_id)

        state = self._providers.get(provider_name)
        if state is None:
            raise ModelNotFound(self._not_found_message(requested_id))

        if enforce_allowlist and not self.is_selectable(provider_name, model_name):
            raise ModelNotAllowed(self._not_allowed_message(requested_id))

        model_info: ModelInfo | None = None
        resolved_name = model_name
        trusted_default = requested is None and model_name == state.provider.model
        if not trusted_default:
            model_info = await state.catalog.resolve_with_info(
                model_name,
                request_id=request_id,
            )
            if model_info is None:
                raise ModelNotFound(self._not_found_message(requested_id))
            resolved_name = model_info.name

        capabilities = merge_capabilities(state.provider.capabilities, model_info)
        model_id = canonical_model_id(provider_name, resolved_name)
        ensure_supported(
            capabilities,
            stream=stream,
            tools=tools,
            has_images=has_images,
            json_mode=json_mode,
            model_id=model_id,
        )
        return ResolvedModel(
            provider=state.provider,
            provider_name=provider_name,
            provider_label=state.label,
            model_id=model_id,
            model=resolved_name,
            capabilities=capabilities,
            context_window=capabilities.context_window,
        )

    def is_selectable(self, provider_name: str, model_name: str) -> bool:
        """O modelo pode ser escolhido explicitamente (allowlist/globs)?"""
        if not self._allowed:
            return True

        model_id = canonical_model_id(provider_name, model_name)
        if _strip_latest(model_id) == _strip_latest(self.default_model_id):
            return True

        for entry in self._allowed:
            expanded = self._aliases.get(entry, entry)
            if "/" in expanded:
                if _glob_match(model_id, expanded):
                    return True
                if _glob_match(f"{provider_name}/{model_name}", expanded):
                    return True
            elif _glob_match(model_name, expanded):
                return True
        return False

    # --- execução -----------------------------------------------------------
    async def generate(
        self,
        resolution: ResolvedModel,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> RoutedResult:
        """Gera em JSON, tentando o fallback somente em falhas de provedor."""
        candidates = await self._candidates(
            resolution,
            stream=False,
            tools=tools,
            request_id=request_id,
        )
        last_error: ProviderError | None = None
        for candidate in candidates:
            try:
                result = await candidate.provider.generate(
                    messages,
                    max_tokens=max_tokens,
                    max_seconds=max_seconds,
                    request_id=request_id,
                    model=candidate.model,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            except ProviderError as exc:
                last_error = exc
                continue
            return RoutedResult(candidate, result)

        if last_error is None:
            raise ProviderError("Nenhum provedor de IA disponível.")
        raise last_error

    async def stream(
        self,
        resolution: ResolvedModel,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        max_seconds: float,
        request_id: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[RoutedChunk]:
        """Streaming com fallback apenas antes do primeiro trecho."""
        candidates = await self._candidates(
            resolution,
            stream=True,
            tools=tools,
            request_id=request_id,
        )
        last_error: ProviderError | None = None

        for candidate in candidates:
            delivered = False
            finished = False
            try:
                async for chunk in candidate.provider.stream(
                    messages,
                    max_tokens=max_tokens,
                    max_seconds=max_seconds,
                    request_id=request_id,
                    model=candidate.model,
                    tools=tools,
                    tool_choice=tool_choice,
                ):
                    if chunk.content or chunk.tool_calls:
                        delivered = True
                    yield RoutedChunk(candidate, chunk)
                    if chunk.finish_reason is not None:
                        finished = True
                        break
            except ProviderError as exc:
                if delivered:
                    raise
                last_error = exc
                continue

            if finished:
                return
            if delivered:
                raise ProviderError("O provedor encerrou o stream sem conclusão.")
            last_error = ProviderError("O provedor encerrou o stream sem conclusão.")

        if last_error is None:
            raise ProviderError("Nenhum provedor de IA disponível.")
        raise last_error

    async def _candidates(
        self,
        primary: ResolvedModel,
        *,
        stream: bool,
        tools: Sequence[ToolSpec] | None,
        request_id: str | None,
    ) -> list[ResolvedModel]:
        candidates = [primary]
        seen = {primary.model_id}
        fallbacks = 0
        for entry in self._fallback_chain:
            if fallbacks >= self._max_retries:
                break
            try:
                candidate = await self.resolve(
                    entry,
                    stream=stream,
                    tools=tools,
                    request_id=request_id,
                    enforce_allowlist=False,
                )
            except (GatewayError, ProviderError):
                continue
            if candidate.model_id in seen:
                continue
            candidates.append(candidate)
            seen.add(candidate.model_id)
            fallbacks += 1
        return candidates

    # --- catálogo e saúde ---------------------------------------------------
    async def list_catalog(
        self,
        *,
        refresh: bool = False,
        request_id: str | None = None,
    ) -> CatalogSnapshot:
        """Agrega catálogos; falha de um provedor vira `available=false`."""
        states = list(self._providers.values())
        results = await asyncio.gather(
            *(
                state.catalog.list_models(refresh=refresh, request_id=request_id)
                for state in states
            ),
            return_exceptions=True,
        )

        entries: list[ProviderCatalogEntry] = []
        errors: list[BaseException] = []
        for state, result in zip(states, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(result)
                entries.append(
                    ProviderCatalogEntry(
                        provider=state.name,
                        label=state.label,
                        available=False,
                        default_model=state.provider.model,
                        models=(),
                        capabilities=state.provider.capabilities,
                        error=_error_code(result),
                    )
                )
                continue
            entries.append(
                ProviderCatalogEntry(
                    provider=state.name,
                    label=state.label,
                    available=True,
                    default_model=_default_name(state, result),
                    models=tuple(result),
                    capabilities=state.provider.capabilities,
                )
            )

        if entries and all(not entry.available for entry in entries):
            first = errors[0] if errors else ProviderUnavailable("Nenhum provedor disponível.")
            if isinstance(first, ProviderError):
                raise first
            raise ProviderUnavailable("Nenhum provedor de IA disponível.") from first

        return CatalogSnapshot(
            default_model_id=self.default_model_id,
            providers=tuple(entries),
        )

    async def list_health(self) -> tuple[ProviderHealth, ...]:
        """Health check independente por provedor (RF-GTW-04)."""
        states = list(self._providers.values())
        results = await asyncio.gather(
            *(state.provider.health() for state in states),
            return_exceptions=True,
        )
        checked_at = self._clock.now()
        health: list[ProviderHealth] = []
        for state, result in zip(states, results, strict=True):
            if isinstance(result, BaseException):
                health.append(
                    ProviderHealth(
                        provider=state.name,
                        label=state.label,
                        default_model=state.provider.model,
                        available=False,
                        checked_at=checked_at,
                        error=_error_code(result),
                    )
                )
            else:
                health.append(
                    ProviderHealth(
                        provider=state.name,
                        label=state.label,
                        default_model=state.provider.model,
                        available=bool(result),
                        checked_at=checked_at,
                    )
                )
        return tuple(health)

    async def health(self) -> bool:
        """`True` somente quando todos os provedores habilitados respondem."""
        health = await self.list_health()
        return all(item.available for item in health)

    # --- internos -----------------------------------------------------------
    def _normalize_model_id(self, value: str) -> str:
        normalized = value.strip()
        normalized = self._aliases.get(normalized, normalized)
        if "/" not in normalized:
            provider = getattr(self, "_default_provider_name", None)
            if provider is None:
                provider = "ollama" if "ollama" in self._providers else next(iter(self._providers))
            normalized = f"{provider}/{normalized}"
        return normalized

    @staticmethod
    def _split(model_id: str) -> tuple[str, str]:
        provider, _, model = model_id.partition("/")
        if not provider or not model:
            raise ModelNotFound(
                f"Model ID inválido: '{model_id}'. Use o formato 'provider/model'."
            )
        return provider, model

    def _not_found_message(self, requested: str) -> str:
        providers = ", ".join(sorted(self._providers))
        return (
            f"Modelo '{requested}' não encontrado. Use 'provider/model'. "
            f"Provedores disponíveis: {providers}."
        )

    def _not_allowed_message(self, requested: str) -> str:
        allowed = ", ".join(self._allowed) if self._allowed else "todos"
        return (
            f"Modelo '{requested}' não permitido por AI_ALLOWED_MODELS. "
            f"Permitidos: {allowed}."
        )


def _glob_match(value: str, pattern: str) -> bool:
    if fnmatchcase(value, pattern):
        return True
    return fnmatchcase(_strip_latest(value), _strip_latest(pattern))


def _default_name(state: _ProviderState, models: Sequence[ModelInfo]) -> str:
    for model in models:
        if model.name == state.provider.model:
            return model.name
    candidate = f"{state.provider.model}:latest"
    for model in models:
        if model.name == candidate:
            return model.name
    return state.provider.model


def _error_code(exc: BaseException) -> str:
    return provider_error_code(exc)


__all__ = [
    "CatalogSnapshot",
    "ProviderCatalogEntry",
    "ProviderHealth",
    "ProviderRouter",
    "ResolvedModel",
    "RoutedChunk",
    "RoutedResult",
    "canonical_model_id",
]

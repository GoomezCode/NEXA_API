"""Catálogo de erros e handlers no padrão Problem Details (SRS §3.8)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message, Scope

PROBLEM_MEDIA_TYPE = "application/problem+json"
DEFAULT_PROBLEM_TYPE = "about:blank"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """Metadados padrão de um código de erro."""

    status: int
    title: str
    detail: str


ERROR_CATALOG: dict[str, ErrorSpec] = {
    "bad_request": ErrorSpec(400, "Requisição inválida", "A requisição está malformada."),
    "invalid_credentials": ErrorSpec(401, "Não autorizado", "Usuário ou senha inválidos."),
    "token_expired": ErrorSpec(401, "Não autorizado", "O token expirou."),
    "invalid_token": ErrorSpec(401, "Não autorizado", "Token ausente ou inválido."),
    "forbidden": ErrorSpec(403, "Acesso negado", "Você não tem permissão para este recurso."),
    "not_found": ErrorSpec(404, "Não encontrado", "O recurso solicitado não existe."),
    "method_not_allowed": ErrorSpec(
        405, "Método não permitido", "O método HTTP não é suportado por este recurso."
    ),
    "payload_too_large": ErrorSpec(
        413, "Corpo muito grande", "O corpo da requisição excede o limite permitido."
    ),
    "unsupported_media_type": ErrorSpec(
        415, "Tipo de mídia não suportado", "Envie o corpo como application/json."
    ),
    "validation_error": ErrorSpec(422, "Requisição inválida", "Um ou mais campos são inválidos."),
    "rate_limited": ErrorSpec(
        429, "Limite excedido", "Muitas requisições em pouco tempo. Tente novamente em instantes."
    ),
    "guest_daily_limit_reached": ErrorSpec(
        429, "Limite diário excedido", "A cota diária de mensagens foi atingida."
    ),
    "ip_daily_limit_reached": ErrorSpec(
        429, "Limite diário excedido", "A cota diária de mensagens deste local/rede foi atingida."
    ),
    "guest_token_limit_reached": ErrorSpec(
        429, "Limite de emissão excedido", "Muitos tokens de convidado emitidos para este IP."
    ),
    "login_locked": ErrorSpec(
        429, "Acesso temporariamente bloqueado", "Muitas tentativas de login. Tente mais tarde."
    ),
    "internal_error": ErrorSpec(500, "Erro interno", "Ocorreu um erro inesperado."),
    "ai_provider_error": ErrorSpec(
        502, "Falha do provedor de IA", "O provedor de IA retornou um erro."
    ),
    "provider_unavailable": ErrorSpec(
        503, "Serviço indisponível", "O provedor de IA não está disponível."
    ),
    "ai_provider_timeout": ErrorSpec(
        504, "Timeout do provedor de IA", "O provedor de IA não respondeu a tempo."
    ),
}


def spec_for(code: str) -> ErrorSpec:
    """Devolve a especificação do código; falha cedo em código desconhecido."""
    try:
        return ERROR_CATALOG[code]
    except KeyError as exc:  # pragma: no cover - erro de programação
        raise ValueError(f"código de erro desconhecido: {code}") from exc


class ValidationErrorItem(BaseModel):
    """Campo inválido no formato de erro do SRS."""

    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str


class ProblemDetails(BaseModel):
    """Corpo padrão de erro (RFC 9457 simplificada)."""

    model_config = ConfigDict(extra="forbid")

    type: str = DEFAULT_PROBLEM_TYPE
    title: str
    status: int
    code: str
    detail: str
    request_id: str = ""
    errors: list[ValidationErrorItem] | None = Field(default=None)
    resets_at: datetime | None = Field(
        default=None, description="Reinício da cota diária (ISO 8601 UTC)."
    )

    @field_serializer("resets_at")
    def _serialize_resets_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ApiError(Exception):
    """Exceção de domínio que vira uma resposta de erro padronizada."""

    def __init__(
        self,
        code: str,
        *,
        detail: str | None = None,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
        errors: Sequence[ValidationErrorItem] | None = None,
        resets_at: datetime | None = None,
    ) -> None:
        spec = spec_for(code)
        self.code = code
        self.status_code = status_code or spec.status
        self.title = spec.title
        self.detail = detail or spec.detail
        self.headers: dict[str, str] = dict(headers or {})
        self.errors: list[ValidationErrorItem] | None = list(errors) if errors else None
        self.resets_at = resets_at
        super().__init__(self.detail)


def request_id_of(request: Request) -> str:
    """Lê o `request_id` colocado no escopo pelo middleware de contexto."""
    state = request.scope.get("state")
    if isinstance(state, dict):
        value = state.get("request_id")
        if isinstance(value, str) and value:
            return value
    return request.headers.get("x-request-id", "")


def problem_payload(
    code: str,
    *,
    detail: str | None = None,
    request_id: str = "",
    status_code: int | None = None,
    errors: Sequence[ValidationErrorItem] | None = None,
    resets_at: datetime | None = None,
) -> dict[str, Any]:
    """Monta o dicionário do corpo de erro."""
    spec = spec_for(code)
    payload = ProblemDetails(
        title=spec.title,
        status=status_code or spec.status,
        code=code,
        detail=detail or spec.detail,
        request_id=request_id,
        errors=list(errors) if errors else None,
        resets_at=resets_at,
    )
    return payload.model_dump(exclude_none=True)


def _problem_response(
    payload: Mapping[str, Any],
    *,
    status_code: int,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    final_headers = {"Cache-Control": "no-store"}
    if headers:
        final_headers.update(headers)
    return JSONResponse(
        content=dict(payload),
        status_code=status_code,
        headers=final_headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _with_auth_challenge(status_code: int, headers: Mapping[str, str] | None) -> dict[str, str]:
    final = dict(headers or {})
    if status_code == 401:
        final.setdefault("WWW-Authenticate", "Bearer")
    return final


_STATUS_CODE_MAP: dict[int, str] = {
    400: "bad_request",
    401: "invalid_token",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "ai_provider_error",
    503: "provider_unavailable",
    504: "ai_provider_timeout",
}

_MAPPED_STATUSES = frozenset(_STATUS_CODE_MAP)


def _status_to_code(status_code: int) -> str:
    if status_code in _STATUS_CODE_MAP:
        return _STATUS_CODE_MAP[status_code]
    return "internal_error" if status_code >= 500 else "bad_request"


def _field_name(loc: Sequence[str | int]) -> str:
    parts = [str(part) for part in loc]
    if parts and parts[0] == "body":
        parts = parts[1:]
    return ".".join(parts) or "body"


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handler para `ApiError`."""
    assert isinstance(exc, ApiError)
    headers = _with_auth_challenge(exc.status_code, exc.headers)
    payload = problem_payload(
        exc.code,
        detail=exc.detail,
        request_id=request_id_of(request),
        status_code=exc.status_code,
        errors=exc.errors,
        resets_at=exc.resets_at,
    )
    return _problem_response(payload, status_code=exc.status_code, headers=headers)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Converte o 422 padrão do FastAPI no formato do SRS."""
    assert isinstance(exc, RequestValidationError)
    errors = [
        ValidationErrorItem(
            field=_field_name(error.get("loc", ())), reason=str(error.get("msg", ""))
        )
        for error in exc.errors()
    ]
    payload = problem_payload(
        "validation_error",
        request_id=request_id_of(request),
        errors=errors,
    )
    return _problem_response(payload, status_code=422)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Padroniza exceções HTTP do Starlette/FastAPI."""
    assert isinstance(exc, StarletteHTTPException)
    code = _status_to_code(exc.status_code)
    # Para status conhecidos usa-se o texto do catálogo; detalhes explícitos de
    # rotas (status fora do catálogo) são preservados quando existirem.
    known = exc.status_code in _MAPPED_STATUSES
    detail = None if known else (exc.detail if isinstance(exc.detail, str) else None)
    headers = _with_auth_challenge(exc.status_code, exc.headers)
    payload = problem_payload(code, detail=detail, request_id=request_id_of(request))
    return _problem_response(payload, status_code=exc.status_code, headers=headers)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Registra e mascara erros inesperados."""
    structlog.get_logger("nexa.errors").exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        request_id=request_id_of(request),
    )
    payload = problem_payload("internal_error", request_id=request_id_of(request))
    return _problem_response(payload, status_code=500)


def register_exception_handlers(app: FastAPI) -> None:
    """Registra todos os handlers na aplicação."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


def send_payload_too_large(scope: Scope) -> tuple[Message, Message]:
    """Mensagens ASGI de `413`, usadas pelo middleware de tamanho de corpo."""
    state = scope.get("state")
    request_id = ""
    if isinstance(state, dict):
        value = state.get("request_id")
        if isinstance(value, str):
            request_id = value
    body = json.dumps(
        problem_payload("payload_too_large", request_id=request_id),
        separators=(",", ":"),
    ).encode("utf-8")
    start: Message = {
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", PROBLEM_MEDIA_TYPE.encode("latin-1")),
            (b"content-length", str(len(body)).encode("latin-1")),
            (b"cache-control", b"no-store"),
            (b"connection", b"close"),
        ],
    }
    end: Message = {"type": "http.response.body", "body": body}
    return start, end


__all__ = [
    "ERROR_CATALOG",
    "PROBLEM_MEDIA_TYPE",
    "ApiError",
    "ErrorSpec",
    "ProblemDetails",
    "ValidationErrorItem",
    "problem_payload",
    "register_exception_handlers",
    "request_id_of",
    "send_payload_too_large",
    "spec_for",
]

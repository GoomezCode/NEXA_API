"""Envelope de erro e respostas de validação da superfície OpenAI (RF-CMP-06/09)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import ValidationErrorItem, spec_for

OPENAI_PATH_PREFIX = "/v1"


@dataclass(frozen=True, slots=True)
class _ErrorShape:
    """Status HTTP, `type` e `param` padrão de um código interno."""

    status: int
    type: str
    param: str | None = None


#: Tradução dos códigos internos para o envelope OpenAI (docs §3.7).
_SHAPES: dict[str, _ErrorShape] = {
    "invalid_api_key": _ErrorShape(401, "authentication_error"),
    "invalid_token": _ErrorShape(401, "authentication_error"),
    "forbidden": _ErrorShape(403, "permission_error"),
    "model_not_allowed": _ErrorShape(403, "permission_error", "model"),
    "not_found": _ErrorShape(404, "invalid_request_error"),
    "model_not_found": _ErrorShape(404, "invalid_request_error", "model"),
    "unsupported_parameter": _ErrorShape(400, "invalid_request_error"),
    "invalid_value": _ErrorShape(400, "invalid_request_error"),
    "bad_request": _ErrorShape(400, "invalid_request_error"),
    "method_not_allowed": _ErrorShape(405, "invalid_request_error"),
    "payload_too_large": _ErrorShape(413, "invalid_request_error"),
    "unsupported_media_type": _ErrorShape(415, "invalid_request_error"),
    "validation_error": _ErrorShape(422, "invalid_request_error"),
    "unsupported_capability": _ErrorShape(422, "invalid_request_error"),
    "rate_limited": _ErrorShape(429, "rate_limit_error"),
    "quota_exceeded": _ErrorShape(429, "rate_limit_error"),
    "guest_daily_limit_reached": _ErrorShape(429, "rate_limit_error"),
    "user_daily_limit_reached": _ErrorShape(429, "rate_limit_error"),
    "ip_daily_limit_reached": _ErrorShape(429, "rate_limit_error"),
    "internal_error": _ErrorShape(500, "api_error"),
    "ai_provider_error": _ErrorShape(502, "api_error"),
    "provider_error": _ErrorShape(502, "api_error"),
    "provider_unavailable": _ErrorShape(503, "api_error"),
    "database_unavailable": _ErrorShape(503, "api_error"),
    "ai_provider_timeout": _ErrorShape(504, "api_error"),
}

#: Mensagens públicas; nunca incluem detalhes do provedor (RF-CMP-09).
_MESSAGES: dict[str, str] = {
    "invalid_api_key": "Chave de API ausente ou inválida.",
    "invalid_token": "Chave de API ausente ou inválida.",
    "forbidden": "Você não tem permissão para este recurso.",
    "model_not_allowed": "O modelo informado não é permitido.",
    "not_found": "O recurso solicitado não existe.",
    "model_not_found": "O modelo informado não existe.",
    "unsupported_parameter": "Parâmetro não suportado pela API.",
    "invalid_value": "Um dos parâmetros informados é inválido.",
    "bad_request": "A requisição está malformada.",
    "method_not_allowed": "O método HTTP não é suportado por este recurso.",
    "payload_too_large": "O corpo da requisição excede o limite permitido.",
    "unsupported_media_type": "Envie o corpo como application/json.",
    "validation_error": "A requisição contém campos inválidos.",
    "unsupported_capability": "O modelo escolhido não suporta a capacidade exigida.",
    "rate_limited": "Muitas requisições em pouco tempo. Tente novamente em instantes.",
    "quota_exceeded": "A cota de uso foi atingida.",
    "guest_daily_limit_reached": "A cota diária de mensagens foi atingida.",
    "user_daily_limit_reached": "A cota diária de mensagens do usuário foi atingida.",
    "ip_daily_limit_reached": "A cota diária de mensagens desta rede foi atingida.",
    "internal_error": "Ocorreu um erro inesperado.",
    "ai_provider_error": "O provedor de IA retornou um erro.",
    "provider_error": "O provedor de IA retornou um erro.",
    "provider_unavailable": "O provedor de IA não está disponível.",
    "database_unavailable": "O banco de usuários não está disponível.",
    "ai_provider_timeout": "O provedor de IA não respondeu a tempo.",
}

_STATUS_SHAPES: dict[int, _ErrorShape] = {
    400: _ErrorShape(400, "invalid_request_error"),
    401: _ErrorShape(401, "authentication_error"),
    403: _ErrorShape(403, "permission_error"),
    404: _ErrorShape(404, "invalid_request_error"),
    405: _ErrorShape(405, "invalid_request_error"),
    413: _ErrorShape(413, "invalid_request_error"),
    415: _ErrorShape(415, "invalid_request_error"),
    422: _ErrorShape(422, "invalid_request_error"),
    429: _ErrorShape(429, "rate_limit_error"),
    500: _ErrorShape(500, "api_error"),
    502: _ErrorShape(502, "api_error"),
    503: _ErrorShape(503, "api_error"),
    504: _ErrorShape(504, "api_error"),
}

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "invalid_api_key",
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


def is_openai_path(path: str) -> bool:
    """`True` para as rotas da superfície OpenAI (`/v1`)."""
    return path == OPENAI_PATH_PREFIX or path.startswith(f"{OPENAI_PATH_PREFIX}/")


class CompatApiError(Exception):
    """Erro de uma camada de compatibilidade, traduzido no envelope externo."""

    def __init__(
        self,
        code: str,
        *,
        message: str | None = None,
        param: str | None = None,
        status_code: int | None = None,
        error_type: str | None = None,
        headers: Mapping[str, str] | None = None,
        retry_after: int | None = None,
    ) -> None:
        shape = _SHAPES.get(code, _ErrorShape(500, "api_error"))
        self.code = code
        self.status_code = status_code or shape.status
        self.error_type = error_type or shape.type
        self.param = param if param is not None else shape.param
        self.message = message or _MESSAGES.get(code) or spec_for(code).detail
        self.headers: dict[str, str] = dict(headers or {})
        self.retry_after = retry_after
        super().__init__(self.message)

    def as_payload(self) -> dict[str, Any]:
        return openai_error_payload(
            self.message,
            error_type=self.error_type,
            code=self.code,
            param=self.param,
        )


def openai_error_payload(
    message: str,
    *,
    error_type: str,
    code: str,
    param: str | None = None,
) -> dict[str, Any]:
    """Corpo `{"error": {...}}` do contrato OpenAI."""
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }


def _response(
    payload: Mapping[str, Any],
    *,
    status_code: int,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    final_headers = {"Cache-Control": "no-store"}
    if headers:
        final_headers.update(headers)
    return JSONResponse(content=dict(payload), status_code=status_code, headers=final_headers)


async def compat_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handler de `CompatApiError`; nunca expõe detalhes internos."""
    assert isinstance(exc, CompatApiError)
    headers = dict(exc.headers)
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return _response(exc.as_payload(), status_code=exc.status_code, headers=headers)


def compat_validation_response(
    request: Request,
    errors: Sequence[ValidationErrorItem],
) -> JSONResponse:
    """422 do FastAPI no envelope OpenAI, com o primeiro campo inválido em `param`."""
    first = errors[0] if errors else None
    param = first.field if first is not None else None
    message = first.reason if first is not None else _MESSAGES["validation_error"]
    payload = openai_error_payload(
        message,
        error_type="invalid_request_error",
        code="validation_error",
        param=param,
    )
    return _response(payload, status_code=422)


def compat_validation_from_exception(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Converte o `RequestValidationError` do FastAPI no envelope OpenAI."""
    errors = [
        ValidationErrorItem(
            field=".".join(str(part) for part in error.get("loc", ()) if part != "body") or "body",
            reason=str(error.get("msg", "")),
        )
        for error in exc.errors()
    ]
    return compat_validation_response(request, errors)


def compat_http_exception_response(
    request: Request,
    *,
    status_code: int,
    detail: str | None = None,
) -> JSONResponse:
    """Respostas HTTP (404/405/...) no envelope OpenAI."""
    shape = _STATUS_SHAPES.get(status_code, _ErrorShape(status_code, "api_error"))
    code = _STATUS_CODES.get(status_code, "internal_error" if status_code >= 500 else "bad_request")
    message = detail or _MESSAGES.get(code) or _MESSAGES["bad_request"]
    payload = openai_error_payload(message, error_type=shape.type, code=code)
    return _response(payload, status_code=status_code)


__all__ = [
    "OPENAI_PATH_PREFIX",
    "CompatApiError",
    "compat_error_handler",
    "compat_http_exception_response",
    "compat_validation_from_exception",
    "compat_validation_response",
    "is_openai_path",
    "openai_error_payload",
]

"""Testes do catálogo de erros e handlers (F0-04)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.core.errors import (
    ERROR_CATALOG,
    ApiError,
    ProblemDetails,
    ValidationErrorItem,
    api_error_handler,
    http_exception_handler,
    problem_payload,
    send_payload_too_large,
    send_unsupported_media_type,
    spec_for,
    unhandled_exception_handler,
    validation_error_handler,
)

REQUEST_ID = "b1f4c7e2-6f0f-4c39-9e57-6a1f0f0b8f4d"


def make_request(*, request_id: str | None = REQUEST_ID) -> Request:
    state: dict[str, Any] = {"request_id": request_id} if request_id else {}
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": "/chat",
            "raw_path": b"/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "state": state,
        }
    )


def body_of(response: Any) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(response.body)
    return parsed


def test_unknown_code_fails_fast() -> None:
    with pytest.raises(ValueError):
        spec_for("does_not_exist")


def test_catalog_codes_have_consistent_status() -> None:
    for code, spec in ERROR_CATALOG.items():
        assert 400 <= spec.status <= 599, code
        assert spec.title
        assert spec.detail


def test_api_error_uses_catalog_defaults() -> None:
    error = ApiError("forbidden")
    assert error.status_code == 403
    assert error.code == "forbidden"
    assert error.detail == ERROR_CATALOG["forbidden"].detail
    assert str(error) == error.detail


def test_api_error_accepts_overrides() -> None:
    error = ApiError(
        "rate_limited",
        detail="Aguarde 3 s.",
        status_code=429,
        headers={"Retry-After": "3"},
        errors=[ValidationErrorItem(field="messages", reason="inválido")],
    )
    assert error.detail == "Aguarde 3 s."
    assert error.headers == {"Retry-After": "3"}
    assert error.errors is not None and len(error.errors) == 1


def test_problem_payload_shape() -> None:
    payload = problem_payload("invalid_token", request_id=REQUEST_ID)
    assert payload["type"] == "about:blank"
    assert payload["status"] == 401
    assert payload["code"] == "invalid_token"
    assert payload["request_id"] == REQUEST_ID
    assert "errors" not in payload


def test_problem_payload_with_errors() -> None:
    payload = problem_payload(
        "validation_error",
        request_id=REQUEST_ID,
        errors=[ValidationErrorItem(field="messages.2.role", reason="deve ser user")],
    )
    assert payload["errors"] == [{"field": "messages.2.role", "reason": "deve ser user"}]


async def test_api_error_handler_adds_bearer_challenge() -> None:
    response = await api_error_handler(make_request(), ApiError("invalid_token"))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == "application/problem+json"
    assert body_of(response)["request_id"] == REQUEST_ID


async def test_api_error_handler_keeps_custom_headers() -> None:
    error = ApiError("rate_limited", headers={"Retry-After": "7"})
    response = await api_error_handler(make_request(), error)
    assert response.headers["retry-after"] == "7"
    assert "www-authenticate" not in response.headers


async def test_api_error_handler_falls_back_to_header() -> None:
    request = make_request(request_id=None)
    request.scope["headers"] = [(b"x-request-id", REQUEST_ID.encode("latin-1"))]
    response = await api_error_handler(request, ApiError("forbidden"))
    assert body_of(response)["request_id"] == REQUEST_ID


async def test_validation_error_handler_maps_fields() -> None:
    error = RequestValidationError(
        [
            {
                "type": "string_type",
                "loc": ("body", "messages", 2, "role"),
                "msg": "deve ser system, user ou assistant",
                "input": 1,
            },
            {
                "type": "missing",
                "loc": ("body",),
                "msg": "campo obrigatório",
                "input": {},
            },
        ]
    )
    response = await validation_error_handler(make_request(), error)
    payload = body_of(response)
    assert response.status_code == 422
    assert payload["code"] == "validation_error"
    assert payload["errors"] == [
        {"field": "messages.2.role", "reason": "deve ser system, user ou assistant"},
        {"field": "body", "reason": "campo obrigatório"},
    ]
    assert payload["request_id"] == REQUEST_ID


async def test_validation_error_handler_keeps_query_prefix() -> None:
    error = RequestValidationError(
        [{"type": "int_parsing", "loc": ("query", "limit"), "msg": "inválido", "input": "x"}]
    )
    response = await validation_error_handler(make_request(), error)
    assert body_of(response)["errors"][0]["field"] == "query.limit"


async def test_http_exception_handler_404() -> None:
    response = await http_exception_handler(make_request(), StarletteHTTPException(404))
    payload = body_of(response)
    assert response.status_code == 404
    assert payload["code"] == "not_found"
    assert payload["detail"] == ERROR_CATALOG["not_found"].detail
    assert payload["request_id"] == REQUEST_ID


async def test_http_exception_handler_400_and_415_are_standardized() -> None:
    bad_request = await http_exception_handler(make_request(), StarletteHTTPException(400))
    assert bad_request.status_code == 400
    assert body_of(bad_request)["code"] == "bad_request"

    unsupported = await http_exception_handler(make_request(), StarletteHTTPException(415))
    assert unsupported.status_code == 415
    payload = body_of(unsupported)
    assert payload["code"] == "unsupported_media_type"
    assert payload["detail"] == ERROR_CATALOG["unsupported_media_type"].detail
    assert payload["request_id"] == REQUEST_ID


async def test_http_exception_handler_401_has_challenge() -> None:
    response = await http_exception_handler(make_request(), StarletteHTTPException(401))
    assert response.headers["www-authenticate"] == "Bearer"
    assert body_of(response)["code"] == "invalid_token"


async def test_http_exception_handler_unknown_status() -> None:
    client_error = await http_exception_handler(make_request(), StarletteHTTPException(418))
    assert client_error.status_code == 418
    assert body_of(client_error)["code"] == "bad_request"

    server_error = await http_exception_handler(make_request(), StarletteHTTPException(599))
    assert body_of(server_error)["code"] == "internal_error"


async def test_unhandled_exception_handler_hides_details() -> None:
    request = make_request()
    request.scope["path"] = "/chat"
    response = await unhandled_exception_handler(request, RuntimeError("segredo interno"))
    payload = body_of(response)
    assert response.status_code == 500
    assert payload["code"] == "internal_error"
    assert "segredo interno" not in json.dumps(payload)


def test_send_payload_too_large_includes_request_id() -> None:
    scope = {"type": "http", "state": {"request_id": REQUEST_ID}}
    start, end = send_payload_too_large(scope)
    assert start["type"] == "http.response.start"
    assert start["status"] == 413
    headers = {key: value for key, value in start["headers"]}
    assert headers[b"content-type"] == b"application/problem+json"
    assert end["type"] == "http.response.body"
    payload = json.loads(end["body"])
    assert payload["code"] == "payload_too_large"
    assert payload["request_id"] == REQUEST_ID


def test_send_unsupported_media_type_includes_request_id() -> None:
    scope = {"type": "http", "state": {"request_id": REQUEST_ID}}
    start, end = send_unsupported_media_type(scope)
    assert start["status"] == 415
    headers = {key: value for key, value in start["headers"]}
    assert headers[b"content-type"] == b"application/problem+json"
    assert headers[b"connection"] == b"close"
    payload = json.loads(end["body"])
    assert payload["code"] == "unsupported_media_type"
    assert payload["request_id"] == REQUEST_ID


def test_send_payload_too_large_without_state() -> None:
    _, end = send_payload_too_large({"type": "http"})
    assert json.loads(end["body"])["request_id"] == ""


def test_problem_details_serializes_absent_reset_as_none() -> None:
    payload = ProblemDetails(
        title="Não autorizado",
        status=401,
        code="invalid_token",
        detail="Token ausente ou inválido.",
    ).model_dump()

    assert payload["resets_at"] is None


def test_problem_payload_with_resets_at() -> None:
    reset = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    payload = problem_payload("guest_daily_limit_reached", request_id=REQUEST_ID, resets_at=reset)
    assert payload["resets_at"] == "2026-10-29T00:00:00Z"


async def test_api_error_handler_includes_resets_at() -> None:
    reset = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    error = ApiError("ip_daily_limit_reached", resets_at=reset)

    response = await api_error_handler(make_request(), error)

    assert response.status_code == 429
    assert body_of(response)["resets_at"] == "2026-10-29T00:00:00Z"

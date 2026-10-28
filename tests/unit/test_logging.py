"""Testes de redaction e logs estruturados (F0-05 / RNF-OBS-02)."""

from __future__ import annotations

import json

import pytest

from app.core.logging import (
    add_request_id,
    bind_request_id,
    configure_logging,
    current_request_id,
    get_logger,
    redact_processor,
    redact_sensitive,
    reset_request_id,
)


def test_redact_sensitive_masks_top_level_keys() -> None:
    event = {
        "password": "segredo",
        "token": "abc",
        "authorization": "Bearer xyz",
        "X-Api-Key": "chave",
        "event": "login",
    }
    redacted = redact_sensitive(event)
    assert redacted == {
        "password": "***",
        "token": "***",
        "authorization": "***",
        "X-Api-Key": "***",
        "event": "login",
    }


def test_redact_sensitive_is_recursive() -> None:
    event = {
        "request": {
            "headers": [{"Authorization": "Bearer abc"}, {"Content-Type": "application/json"}],
            "password": "x",
        },
        "messages": [{"role": "user", "content": "texto privado"}],
        "cloud_api_key": "sk-123",
        "prompt": "segredo",
    }
    redacted = redact_sensitive(event)
    assert redacted["request"]["password"] == "***"
    assert redacted["request"]["headers"][0]["Authorization"] == "***"
    assert redacted["request"]["headers"][1]["Content-Type"] == "application/json"
    assert redacted["messages"] == "***"
    assert redacted["cloud_api_key"] == "***"
    assert redacted["prompt"] == "***"


def test_redact_sensitive_keeps_scalars_and_unknown_keys() -> None:
    assert redact_sensitive("texto") == "texto"
    assert redact_sensitive(42) == 42
    assert redact_sensitive(None) is None
    assert redact_sensitive({"role": "user"}) == {"role": "user"}


def test_add_request_id_uses_context() -> None:
    token = bind_request_id("req-1")
    try:
        assert current_request_id() == "req-1"
        event = add_request_id(None, "info", {"event": "teste"})
        assert event["request_id"] == "req-1"
    finally:
        reset_request_id(token)
    assert current_request_id() == ""


def test_add_request_id_does_not_override_explicit_value() -> None:
    token = bind_request_id("req-1")
    try:
        event = add_request_id(None, "info", {"event": "teste", "request_id": "req-2"})
        assert event["request_id"] == "req-2"
    finally:
        reset_request_id(token)


def test_redact_processor_masks_event() -> None:
    processed = redact_processor(None, "info", {"event": "login", "password": "x"})
    assert processed == {"event": "login", "password": "***"}


def test_configure_logging_emits_json_with_request_id(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG")
    token = bind_request_id("req-json")
    try:
        get_logger("test").info("evento", password="segredo", content="texto")
    finally:
        reset_request_id(token)

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines
    payload = json.loads(lines[-1])
    assert payload["event"] == "evento"
    assert payload["level"] == "info"
    assert payload["request_id"] == "req-json"
    assert payload["password"] == "***"
    assert payload["content"] == "***"
    assert "timestamp" in payload
    assert "segredo" not in captured.out
    assert "texto" not in captured.out

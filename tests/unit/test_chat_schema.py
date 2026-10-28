"""Testes dos schemas do chat (RF-CHAT-04/09)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.chat import ChatRequest


def test_accepts_minimal_request_and_defaults_to_stream() -> None:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "Olá"}]})

    assert request.stream is True
    assert request.messages[0].role == "user"
    assert request.messages[0].content == "Olá"


def test_accepts_json_mode() -> None:
    request = ChatRequest.model_validate(
        {
            "messages": [
                {"role": "user", "content": "Oi"},
                {"role": "assistant", "content": "Olá!"},
                {"role": "user", "content": "Tudo bem?"},
            ],
            "stream": False,
        }
    )
    assert request.stream is False
    assert len(request.messages) == 3


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": []},
        {"messages": [{"role": "user"}]},
        {"messages": [{"role": "root", "content": "oi"}]},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "user", "content": "oi"}], "system_prompt": "x"},
        {"messages": [{"role": "user", "content": "oi"}], "max_tokens": 10},
        {"messages": [{"role": "user", "content": "oi", "extra": 1}]},
        {
            "messages": [
                {"role": "user", "content": "oi"},
                {"role": "assistant", "content": "olá"},
            ],
            "stream": False,
        },
    ],
)
def test_rejects_invalid_bodies(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(body)


def test_last_message_must_be_user() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ChatRequest.model_validate(
            {
                "messages": [
                    {"role": "user", "content": "oi"},
                    {"role": "assistant", "content": "olá"},
                ]
            }
        )

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("messages",) for error in errors)


def test_system_message_may_appear_before_user() -> None:
    request = ChatRequest.model_validate(
        {
            "messages": [
                {"role": "system", "content": "seja breve"},
                {"role": "user", "content": "oi"},
            ]
        }
    )
    assert request.messages[0].role == "system"

"""Contratos de imagem no chat nativo e serialização multimodal."""

import base64

import pytest
from pydantic import ValidationError

from app.ai.anthropic import _content_blocks
from app.ai.anthropic import parse_models as parse_anthropic_models
from app.ai.base import ChatMessage, ImageData
from app.ai.ollama import _message_payload as ollama_message_payload
from app.ai.openai import _message_payload as openai_message_payload
from app.ai.openai import parse_models as parse_openai_models
from app.schemas.chat import ChatRequest

PNG = b"\x89PNG\r\n\x1a\n" + b"minimal"
ENCODED = base64.b64encode(PNG).decode("ascii")


def test_native_chat_accepts_text_and_image_parts() -> None:
    request = ChatRequest.model_validate(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Descreva esta imagem."},
                        {"type": "image", "media_type": "image/png", "data": ENCODED},
                    ],
                }
            ]
        }
    )
    assert request.messages[0].text_content == "Descreva esta imagem."
    assert request.messages[0].images[0].media_type == "image/png"


def test_native_chat_rejects_mime_mismatch_and_svg() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "media_type": "image/jpeg", "data": ENCODED},
                        ],
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "media_type": "image/svg+xml", "data": ENCODED},
                        ],
                    }
                ]
            }
        )


def test_native_chat_limits_images_per_message() -> None:
    parts = [{"type": "image", "media_type": "image/png", "data": ENCODED} for _ in range(5)]
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"messages": [{"role": "user", "content": parts}]})


def test_provider_payloads_receive_the_image_bytes() -> None:
    message = ChatMessage(
        role="user", content="Descreva", images=(ImageData("image/png", ENCODED),)
    )
    assert ollama_message_payload(message)["images"] == [ENCODED]
    openai = openai_message_payload(message)["content"]
    assert openai[1]["image_url"]["url"] == f"data:image/png;base64,{ENCODED}"
    anthropic = _content_blocks(message)
    assert anthropic[1]["source"] == {"type": "base64", "media_type": "image/png", "data": ENCODED}


def test_vision_capability_is_reported_per_model() -> None:
    openai = parse_openai_models({"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-3.5-turbo"}]})
    openai_caps = {model.name: model.capabilities for model in openai}
    assert openai_caps["gpt-4o-mini"] is not None and openai_caps["gpt-4o-mini"].vision
    assert openai_caps["gpt-3.5-turbo"] is not None and not openai_caps["gpt-3.5-turbo"].vision

    anthropic = parse_anthropic_models(
        {"data": [{"id": "claude-3-7-sonnet"}, {"id": "claude-2.1"}]}
    )
    anthropic_caps = {model.name: model.capabilities for model in anthropic}
    assert (
        anthropic_caps["claude-3-7-sonnet"] is not None
        and anthropic_caps["claude-3-7-sonnet"].vision
    )
    assert anthropic_caps["claude-2.1"] is not None and not anthropic_caps["claude-2.1"].vision

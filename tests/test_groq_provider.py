"""Groq responses are normalized without making network requests."""

from __future__ import annotations

import json

import httpx

from src.config import get_settings
from src.groq_provider import GROQ_CHAT_COMPLETIONS_URL, complete_groq


def test_groq_is_preferred_when_key_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    settings = get_settings()

    assert settings.llm_provider == "groq"
    assert settings.active_llm_model == settings.GROQ_MODEL


async def test_groq_tool_call_is_normalized() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == GROQ_CHAT_COMPLETIONS_URL
        assert request.headers["Authorization"] == "Bearer test-groq-key"
        payload = json.loads(request.content)
        assert payload["tools"][0]["type"] == "function"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-vendor",
                                    "type": "function",
                                    "function": {
                                        "name": "validate_vendor",
                                        "arguments": '{"vendor_id":1}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response = await complete_groq(
            api_key="test-groq-key",
            model="openai/gpt-oss-20b",
            max_tokens=128,
            system_prompt="Use tools",
            tools=[tool_definition()],
            messages=[{"role": "user", "content": "Process invoice"}],
            client=client,
        )

    assert response.stop_reason == "tool_use"
    assert response.content[0].name == "validate_vendor"
    assert response.content[0].input == {"vendor_id": 1}
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7


async def test_groq_stream_emits_text_deltas() -> None:
    events = [
        {
            "choices": [
                {
                    "delta": {"content": '{"decision":"'},
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"content": 'approved","reason":"ok"}'},
                    "finish_reason": "stop",
                }
            ]
        },
        {
            "choices": [],
            "usage": {"prompt_tokens": 13, "completion_tokens": 6},
        },
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    body += "data: [DONE]\n\n"

    async def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    deltas: list[str] = []

    async def on_delta(delta: str) -> None:
        deltas.append(delta)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response = await complete_groq(
            api_key="test-groq-key",
            model="openai/gpt-oss-20b",
            max_tokens=128,
            system_prompt="Return JSON",
            tools=[tool_definition()],
            messages=[{"role": "user", "content": "Decide"}],
            on_delta=on_delta,
            client=client,
        )

    assert response.stop_reason == "end_turn"
    assert response.content[0].text == '{"decision":"approved","reason":"ok"}'
    assert deltas == ['{"decision":"', 'approved","reason":"ok"}']
    assert response.usage.input_tokens == 13
    assert response.usage.output_tokens == 6


def tool_definition() -> dict:
    return {
        "name": "validate_vendor",
        "description": "Validate a vendor",
        "input_schema": {
            "type": "object",
            "properties": {"vendor_id": {"type": "integer"}},
            "required": ["vendor_id"],
        },
    }

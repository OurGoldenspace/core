"""Direct Groq API adapter normalized to the agent runtime message shape."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import httpx

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"


async def complete_groq(
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    system_prompt: str,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    client: httpx.AsyncClient | None = None,
) -> SimpleNamespace:
    """Call Groq and return the provider-neutral shape consumed by MaintenanceAgent."""
    request = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            *_groq_messages(messages),
        ],
        "tools": [_groq_tool(tool) for tool in tools],
        "tool_choice": "auto",
    }

    if client is not None:
        return await _complete_with_client(
            client=client,
            api_key=api_key,
            request=request,
            on_delta=on_delta,
        )

    # MaintenanceAgent owns the end-to-end timeout so streaming is not cut off by
    # httpx's shorter default read timeout.
    async with httpx.AsyncClient(timeout=None) as owned_client:
        return await _complete_with_client(
            client=owned_client,
            api_key=api_key,
            request=request,
            on_delta=on_delta,
        )


async def _complete_with_client(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    request: dict[str, Any],
    on_delta: Callable[[str], Awaitable[None]] | None,
) -> SimpleNamespace:
    headers = {"Authorization": f"Bearer {api_key}"}
    if on_delta is None:
        response = await client.post(
            GROQ_CHAT_COMPLETIONS_URL,
            headers=headers,
            json=request,
        )
        response.raise_for_status()
        return _normalize_response(response.json())

    stream_request = {
        **request,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    return await _stream_response(
        client=client,
        headers=headers,
        request=stream_request,
        on_delta=on_delta,
    )


async def _stream_response(
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    request: dict[str, Any],
    on_delta: Callable[[str], Awaitable[None]],
) -> SimpleNamespace:
    text_parts: list[str] = []
    tool_calls: dict[int, dict[str, str]] = {}
    finish_reason = "stop"
    usage: dict[str, int] = {}

    async with client.stream(
        "POST",
        GROQ_CHAT_COMPLETIONS_URL,
        headers=headers,
        json=request,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw_event = line.removeprefix("data:").strip()
            if raw_event == "[DONE]":
                break

            event = json.loads(raw_event)
            if event.get("usage"):
                usage = event["usage"]
            choices = event.get("choices", [])
            if not choices:
                continue

            choice = choices[0]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta", {})
            content = delta.get("content")
            if content:
                text_parts.append(content)
                await on_delta(content)

            for tool_delta in delta.get("tool_calls") or []:
                index = int(tool_delta.get("index", 0))
                accumulated = tool_calls.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                accumulated["id"] += tool_delta.get("id") or ""
                function = tool_delta.get("function") or {}
                accumulated["name"] += function.get("name") or ""
                accumulated["arguments"] += function.get("arguments") or ""

    return _normalized_message(
        finish_reason=finish_reason,
        text="".join(text_parts),
        tool_calls=[tool_calls[index] for index in sorted(tool_calls)],
        usage=usage,
    )


def _normalize_response(payload: dict[str, Any]) -> SimpleNamespace:
    choice = payload["choices"][0]
    message = choice["message"]
    tool_calls = [
        {
            "id": item["id"],
            "name": item["function"]["name"],
            "arguments": item["function"].get("arguments", "{}"),
        }
        for item in message.get("tool_calls") or []
    ]
    return _normalized_message(
        finish_reason=choice.get("finish_reason", "stop"),
        text=message.get("content") or "",
        tool_calls=tool_calls,
        usage=payload.get("usage") or {},
    )


def _normalized_message(
    *,
    finish_reason: str,
    text: str,
    tool_calls: list[dict[str, str]],
    usage: dict[str, int],
) -> SimpleNamespace:
    content: list[SimpleNamespace] = []
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    for item in tool_calls:
        content.append(
            SimpleNamespace(
                type="tool_use",
                id=item["id"],
                name=item["name"],
                input=_parse_arguments(item["arguments"]),
            )
        )
    return SimpleNamespace(
        stop_reason="tool_use" if tool_calls else "end_turn",
        content=content,
        usage=SimpleNamespace(
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        ),
        provider_finish_reason=finish_reason,
    )


def _parse_arguments(arguments: str) -> dict[str, Any]:
    try:
        payload = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return {"_invalid_json": arguments}
    return payload if isinstance(payload, dict) else {"_invalid_json": arguments}


def _groq_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


def _groq_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            converted.append({"role": message["role"], "content": content})
            continue

        if message["role"] == "assistant":
            converted.append(_groq_assistant_message(content))
            continue

        for block in content:
            if block.get("type") != "tool_result":
                continue
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": block["content"],
                }
            )
    return converted


def _groq_assistant_message(content: list[Any]) -> dict[str, Any]:
    text = "".join(
        getattr(block, "text", "")
        for block in content
        if getattr(block, "type", None) == "text"
    )
    tool_calls = [
        {
            "id": block.id,
            "type": "function",
            "function": {
                "name": block.name,
                "arguments": json.dumps(block.input, default=str),
            },
        }
        for block in content
        if getattr(block, "type", None) == "tool_use"
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


async def complete_groq_chat(
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    messages: list[dict[str, Any]],
    timeout_seconds: float = 30,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Plain chat completion for intake. Supports text and image_url parts."""
    request = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "messages": messages,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    async def _post(http_client: httpx.AsyncClient) -> str:
        response = await http_client.post(
            GROQ_CHAT_COMPLETIONS_URL,
            headers=headers,
            json=request,
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["choices"][0]["message"].get("content") or "")

    if client is not None:
        return await _post(client)
    async with httpx.AsyncClient(timeout=timeout_seconds) as owned_client:
        return await _post(owned_client)

"""
Assemble the agent transcript inside a token budget.

Oldest tool results are truncated first. The latest user turn and the
most recent assistant tool-call stay intact so the model can still act.
"""

from __future__ import annotations

from typing import Any


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("content", item)))
            else:
                parts.append(str(getattr(item, "text", item)))
        return " ".join(parts)
    return str(content)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(_content_text(message.get("content", ""))) for message in messages)


def trim_messages(messages: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """
    Copy messages and replace oldest tool_result payloads until under budget.

    This is the contract for context assembly: we never silently drop the
    latest turn, and we never send unbounded tool transcripts to the model.
    """
    if estimate_messages_tokens(messages) <= budget:
        return messages

    trimmed = [dict(message) for message in messages]
    for index, message in enumerate(trimmed):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        new_content = []
        changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                new_content.append(
                    {
                        **block,
                        "content": "[truncated to fit token budget]",
                    }
                )
                changed = True
            else:
                new_content.append(block)
        if changed:
            trimmed[index] = {**message, "content": new_content}
        if estimate_messages_tokens(trimmed) <= budget:
            return trimmed
    return trimmed

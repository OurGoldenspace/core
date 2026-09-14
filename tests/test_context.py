from src.context import estimate_messages_tokens, trim_messages


def test_trim_messages_keeps_latest_turn_under_budget() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "x" * 4000},
            ],
        },
        {"role": "user", "content": "decide now"},
    ]
    trimmed = trim_messages(messages, budget=50)
    assert estimate_messages_tokens(trimmed) <= 80
    assert trimmed[-1]["content"] == "decide now"
    assert trimmed[0]["content"][0]["content"] == "[truncated to fit token budget]"


def test_trim_messages_noop_when_under_budget() -> None:
    messages = [{"role": "user", "content": "short"}]
    assert trim_messages(messages, budget=1000) == messages

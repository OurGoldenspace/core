"""Provider text deltas are forwarded while the final message accumulates."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from src.agent import InvoiceAgent


class FakeMessageStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    @property
    async def text_stream(self):
        for delta in ('{"decision":', '"approved"}'):
            yield delta

    async def get_final_message(self):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class FakeMessages:
    def stream(self, **_):
        return FakeMessageStream()


class FakeClient:
    messages = FakeMessages()


async def test_anthropic_text_deltas_reach_event_callback() -> None:
    events = []
    agent = InvoiceAgent(db=None, tenant_id=1, on_event=events.append)
    agent._use_policy = False
    agent.client = FakeClient()

    response = await agent._complete(
        messages=[{"role": "user", "content": "process"}],
        last_results=None,
        invoice_id="INV-STREAM",
        vendor_id=1,
        department_id=1,
        amount=Decimal("100"),
        date="2024-09-13",
    )

    assert response.stop_reason == "end_turn"
    assert events == [
        {"type": "model_delta", "delta": '{"decision":'},
        {"type": "model_delta", "delta": '"approved"}'},
    ]

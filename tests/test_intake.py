from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.intake import apply_user_text, conversational_reply, interpret_turn, missing_slots, next_question


UNITS = [
    {"unit_id": 1, "name": "4B", "property_name": "Harborview Apartments"},
    {"unit_id": 2, "name": "12A", "property_name": "Harborview Apartments"},
    {"unit_id": 3, "name": "2C", "property_name": "Maple Court"},
]
VENDORS = [
    {"vendor_id": 1, "name": "Acme Corp Supplies", "is_approved": True, "risk_level": "low"},
    {"vendor_id": 20, "name": "Vendor Management Inc", "is_approved": False, "risk_level": "high"},
]


def test_incomplete_message_asks_for_unit() -> None:
    draft = apply_user_text({}, "Heat is out", UNITS, VENDORS, today=date(2024, 9, 13))
    assert draft["message"] == "Heat is out"
    assert "unit_id" in missing_slots(draft)
    assert "unit" in next_question(draft, UNITS).lower()


def test_one_message_can_fill_every_slot() -> None:
    draft = apply_user_text(
        {},
        "Heat is out in 4B. Send Acme Corp Supplies, $2500 today.",
        UNITS,
        VENDORS,
        today=date(2024, 9, 13),
    )
    assert missing_slots(draft) == []
    assert draft["unit_id"] == 1
    assert draft["vendor_id"] == 1
    assert draft["amount"] == Decimal("2500")
    assert draft["date"] == "2024-09-13"
    assert next_question(draft, UNITS) is None


def test_reply_acknowledges_the_problem() -> None:
    draft = apply_user_text({}, "Heat is out", UNITS, VENDORS, today=date(2024, 9, 13))
    reply = conversational_reply(draft, UNITS, "Heat is out")
    assert "heat" in reply.lower()
    assert "unit" in reply.lower()


async def test_llm_extraction_is_grounded_to_catalog() -> None:
    async def fake_llm(**_kwargs) -> str:
        return """
        {"reply":"Harborview 4B with Acme at $2500.","problem":"No heat in the apartment",
         "unit_id":1,"vendor_id":1,"amount":2500,"date":"2024-09-13","ready":true}
        """

    draft, reply, ready = await interpret_turn(
        draft={},
        user_text="the heat died in the harbor place, unit B",
        units=UNITS,
        vendors=VENDORS,
        today=date(2024, 9, 13),
        llm_complete=fake_llm,
    )
    assert ready is True
    assert draft["unit_id"] == 1
    assert draft["vendor_id"] == 1
    assert draft["amount"] == Decimal("2500")


async def test_llm_cannot_invent_a_unit() -> None:
    async def fake_llm(**_kwargs) -> str:
        return '{"reply":"Filing now.","problem":"Something broke","unit_id":99,"vendor_id":1,"amount":100,"date":"2024-09-13"}'

    draft, _reply, ready = await interpret_turn(
        draft={},
        user_text="something broke",
        units=UNITS,
        vendors=VENDORS,
        today=date(2024, 9, 13),
        llm_complete=fake_llm,
    )
    assert ready is False
    assert draft["unit_id"] is None

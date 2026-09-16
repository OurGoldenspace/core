"""Turn a chat into a structured maintenance request, one missing field at a time."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

SLOT_ORDER = ("message", "unit_id", "vendor_id", "amount", "date")

_AMOUNT = re.compile(
    r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)"
    r"|([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)\s*(?:dollars|usd)\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_VENDOR_ID = re.compile(r"\b(?:vendor|contractor)\s*#?\s*(\d+)\b", re.IGNORECASE)
_UNIT_ID = re.compile(r"\bunit\s*#?\s*(\d+)\b", re.IGNORECASE)


def empty_draft() -> dict[str, Any]:
    return {
        "message": "",
        "unit_id": None,
        "unit_label": "",
        "vendor_id": None,
        "vendor_name": "",
        "amount": None,
        "date": "",
    }


def merge_draft(base: dict[str, Any] | None, updates: dict[str, Any]) -> dict[str, Any]:
    draft = empty_draft()
    draft.update({key: value for key, value in (base or {}).items() if key in draft})
    draft.update({key: value for key, value in updates.items() if key in draft})
    return draft


def apply_user_text(
    draft: dict[str, Any],
    text: str,
    units: list[dict[str, Any]],
    vendors: list[dict[str, Any]],
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    next_draft = merge_draft(draft, {})
    lowered = text.lower()

    unit = _match_unit(text, units)
    if unit is not None:
        next_draft["unit_id"] = unit["unit_id"]
        next_draft["unit_label"] = f"{unit['property_name']} {unit['name']}"

    vendor = _match_vendor(text, vendors)
    if vendor is not None:
        next_draft["vendor_id"] = vendor["vendor_id"]
        next_draft["vendor_name"] = vendor["name"]

    amount = _match_amount(text)
    if amount is not None:
        next_draft["amount"] = amount

    found_date = _match_date(lowered, today)
    if found_date:
        next_draft["date"] = found_date

    remainder = _remainder(text, next_draft, units, vendors)
    if len(remainder) >= 8:
        if next_draft["message"]:
            if remainder.lower() not in next_draft["message"].lower():
                next_draft["message"] = f"{next_draft['message']} {remainder}".strip()
        else:
            next_draft["message"] = remainder

    return next_draft


def missing_slots(draft: dict[str, Any]) -> list[str]:
    missing = []
    if not str(draft.get("message") or "").strip():
        missing.append("message")
    if draft.get("unit_id") is None:
        missing.append("unit_id")
    if draft.get("vendor_id") is None:
        missing.append("vendor_id")
    if draft.get("amount") is None:
        missing.append("amount")
    if not str(draft.get("date") or "").strip():
        missing.append("date")
    return missing


def next_question(draft: dict[str, Any], units: list[dict[str, Any]]) -> str | None:
    missing = missing_slots(draft)
    if not missing:
        return None
    slot = missing[0]
    if slot == "message":
        return "What needs to be fixed?"
    if slot == "unit_id":
        labels = ", ".join(f"{item['property_name']} {item['name']}" for item in units)
        return f"Which unit is this for? {labels}."
    if slot == "vendor_id":
        return (
            "Which contractor should we send? "
            "For the demo, try Acme Corp Supplies or Vendor Management Inc."
        )
    if slot == "amount":
        return "What is the estimated cost?"
    return "What date should this be filed? You can say today."


def ready_summary(draft: dict[str, Any]) -> str:
    amount = draft.get("amount")
    amount_label = f"${Decimal(str(amount)):,.2f}" if amount is not None else "unknown"
    return (
        f"I have {draft.get('unit_label') or 'the unit'}, "
        f"{draft.get('vendor_name') or 'the contractor'}, "
        f"{amount_label} on {draft.get('date')}. Checking the PMS now."
    )


def _match_amount(text: str) -> Decimal | None:
    match = _AMOUNT.search(text)
    if match is None:
        return None
    raw = (match.group(1) or match.group(2) or "").replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if value <= 0 or value > Decimal("999999.99"):
        return None
    return value


def _match_date(lowered: str, today: date) -> str:
    if re.search(r"\b(today|asap|now)\b", lowered):
        return today.isoformat()
    if re.search(r"\btomorrow\b", lowered):
        return (today + timedelta(days=1)).isoformat()
    match = _ISO_DATE.search(lowered)
    return match.group(1) if match else ""


def _match_unit(text: str, units: list[dict[str, Any]]) -> dict[str, Any] | None:
    id_match = _UNIT_ID.search(text)
    if id_match:
        unit_id = int(id_match.group(1))
        for unit in units:
            if unit["unit_id"] == unit_id:
                return unit
    lowered = text.lower()
    hits: list[dict[str, Any]] = []
    for unit in units:
        name = re.escape(str(unit["name"]))
        if re.search(rf"\b{name}\b", text, re.IGNORECASE):
            hits.append(unit)
    unique = _unique_by_id(hits, "unit_id")
    if len(unique) == 1:
        return unique[0]
    property_hits: list[dict[str, Any]] = []
    for unit in units:
        property_name = str(unit["property_name"]).lower()
        if property_name and property_name in lowered:
            property_hits.append(unit)
    unique_property = _unique_by_id(property_hits, "unit_id")
    if len(unique_property) == 1:
        return unique_property[0]
    return None


def _match_vendor(text: str, vendors: list[dict[str, Any]]) -> dict[str, Any] | None:
    id_match = _VENDOR_ID.search(text)
    if id_match:
        vendor_id = int(id_match.group(1))
        for vendor in vendors:
            if vendor["vendor_id"] == vendor_id:
                return vendor
    lowered = text.lower()
    hits = [
        vendor
        for vendor in vendors
        if str(vendor["name"]).lower() in lowered
    ]
    unique = _unique_by_id(hits, "vendor_id")
    if len(unique) == 1:
        return unique[0]
    token_hits: list[dict[str, Any]] = []
    for vendor in vendors:
        for token in re.findall(r"[a-z0-9]{4,}", str(vendor["name"]).lower()):
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                token_hits.append(vendor)
                break
    unique_tokens = _unique_by_id(token_hits, "vendor_id")
    if len(unique_tokens) == 1:
        return unique_tokens[0]
    return None


def _remainder(
    text: str,
    draft: dict[str, Any],
    units: list[dict[str, Any]],
    vendors: list[dict[str, Any]],
) -> str:
    cleaned = text
    for unit in units:
        cleaned = re.sub(rf"\b{re.escape(str(unit['name']))}\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(re.escape(str(unit["property_name"])), " ", cleaned, flags=re.IGNORECASE)
    if draft.get("vendor_name"):
        cleaned = re.sub(re.escape(str(draft["vendor_name"])), " ", cleaned, flags=re.IGNORECASE)
    cleaned = _AMOUNT.sub(" ", cleaned)
    cleaned = _ISO_DATE.sub(" ", cleaned)
    cleaned = re.sub(r"\b(today|tomorrow|asap|now|vendor|contractor|unit|#)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,")
    return cleaned


def _unique_by_id(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: dict[Any, dict[str, Any]] = {}
    for item in items:
        seen[item[key]] = item
    return list(seen.values())


def conversational_reply(draft: dict[str, Any], units: list[dict[str, Any]], user_text: str) -> str:
    text = (user_text or "").lower()
    ack = ""
    if any(word in text for word in ("heat", "hvac", "furnace", "cold", "radiator")):
        ack = "No heat is urgent."
    elif "boiler" in text:
        ack = "A boiler job needs a clear estimate."
    elif any(word in text for word in ("leak", "water", "pipe", "flood", "drip")):
        ack = "I'll treat this as a water issue."
    elif any(word in text for word in ("photo", "picture", "image", "attached")):
        ack = "Thanks for the extra detail."
    elif str(draft.get("message") or "").strip():
        ack = f"Got it: {str(draft['message']).strip()[:90]}."
    question = next_question(draft, units) or ""
    return f"{ack} {question}".strip()


def merge_llm_extraction(
    draft: dict[str, Any],
    extracted: dict[str, Any],
    units: list[dict[str, Any]],
    vendors: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    next_draft = merge_draft(draft, {})
    problem = str(extracted.get("problem") or extracted.get("message") or "").strip()
    if len(problem) >= 8:
        next_draft["message"] = problem[:4_000]

    unit = _unit_from_extraction(extracted, units)
    if unit is not None:
        next_draft["unit_id"] = unit["unit_id"]
        next_draft["unit_label"] = f"{unit['property_name']} {unit['name']}"

    vendor = _vendor_from_extraction(extracted, vendors)
    if vendor is not None:
        next_draft["vendor_id"] = vendor["vendor_id"]
        next_draft["vendor_name"] = vendor["name"]

    amount = _amount_from_extraction(extracted)
    if amount is not None:
        next_draft["amount"] = amount

    found_date = _date_from_extraction(extracted, today)
    if found_date:
        next_draft["date"] = found_date
    return next_draft


def parse_intake_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


async def interpret_turn(
    *,
    draft: dict[str, Any],
    user_text: str,
    units: list[dict[str, Any]],
    vendors: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
    image: dict[str, str] | None = None,
    today: date | None = None,
    llm_complete: Any = None,
) -> tuple[dict[str, Any], str, bool]:
    """Understand the latest turn, fill a grounded draft, and decide whether to run tools."""
    today = today or date.today()
    next_draft = apply_user_text(draft, user_text or "", units, vendors, today)
    reply = conversational_reply(next_draft, units, user_text)
    extracted: dict[str, Any] = {}

    complete = llm_complete
    if complete is None and _provider_can_chat():
        complete = _groq_intake_complete

    if complete is not None:
        try:
            raw = await complete(
                history=history or [],
                user_text=user_text or "",
                draft=next_draft,
                units=units,
                vendors=vendors,
                today=today,
                image=image,
            )
            extracted = parse_intake_json(raw)
        except Exception:
            extracted = {}

    if extracted:
        next_draft = merge_llm_extraction(next_draft, extracted, units, vendors, today)
        llm_reply = str(extracted.get("reply") or "").strip()
        if llm_reply:
            reply = llm_reply
        else:
            reply = conversational_reply(next_draft, units, user_text)
    elif image and not (user_text or "").strip() and complete is None:
        reply = (
            "I received a photo, but this runtime has no vision model. "
            "Describe the damage, unit, contractor, estimate, and date."
        )

    ready = not missing_slots(next_draft)
    if ready:
        return next_draft, ready_summary(next_draft), True
    return next_draft, reply, False


def _provider_can_chat() -> bool:
    from src.config import settings

    return settings.llm_provider == "groq"


async def _groq_intake_complete(
    *,
    history: list[dict[str, str]],
    user_text: str,
    draft: dict[str, Any],
    units: list[dict[str, Any]],
    vendors: list[dict[str, Any]],
    today: date,
    image: dict[str, str] | None,
) -> str:
    from src.config import settings
    from src.groq_provider import complete_groq_chat

    catalog = {
        "units": [
            {"unit_id": item["unit_id"], "name": item["name"], "property": item["property_name"]}
            for item in units
        ],
        "vendors": [
            {"vendor_id": item["vendor_id"], "name": item["name"]}
            for item in vendors[:20]
        ],
        "already_known": {
            "problem": draft.get("message") or None,
            "unit_id": draft.get("unit_id"),
            "vendor_id": draft.get("vendor_id"),
            "amount": str(draft["amount"]) if draft.get("amount") is not None else None,
            "date": draft.get("date") or None,
        },
        "today": today.isoformat(),
    }
    system = (
        "You are a WorkCore maintenance coordinator chatting with a property manager or tenant. "
        "Understand their meaning, including messy phrasing and photos of damage. "
        "Never invent a unit or contractor that is not in the catalog. "
        "Ask only for what is still missing. Keep reply to 1-2 short sentences. "
        "Return JSON only with keys: reply, problem, unit_id, vendor_id, amount, date, ready."
    )
    user_prompt = (
        f"Catalog and current draft:\n{json.dumps(catalog, default=str)}\n\n"
        f"Latest message: {user_text or '(photo attached, no text)'}"
    )
    user_content: Any
    if image:
        user_content = [
            {"type": "text", "text": user_prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image['media_type']};base64,{image['data']}",
                },
            },
        ]
        model = settings.GROQ_VISION_MODEL
    else:
        user_content = user_prompt
        model = settings.GROQ_MODEL

    messages = [{"role": "system", "content": system}]
    for item in history[-6:]:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_content})
    return await complete_groq_chat(
        api_key=settings.GROQ_API_KEY,
        model=model,
        max_tokens=min(400, settings.LLM_MAX_TOKENS),
        messages=messages,
        timeout_seconds=float(settings.LLM_TIMEOUT_SECONDS),
    )


def _unit_from_extraction(extracted: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any] | None:
    unit_id = extracted.get("unit_id")
    if isinstance(unit_id, int):
        for unit in units:
            if unit["unit_id"] == unit_id:
                return unit
    blob = " ".join(
        str(extracted.get(key) or "")
        for key in ("unit", "unit_name", "unit_label", "problem", "reply")
    )
    return _match_unit(blob, units) if blob.strip() else None


def _vendor_from_extraction(extracted: dict[str, Any], vendors: list[dict[str, Any]]) -> dict[str, Any] | None:
    vendor_id = extracted.get("vendor_id")
    if isinstance(vendor_id, int):
        for vendor in vendors:
            if vendor["vendor_id"] == vendor_id:
                return vendor
    blob = " ".join(
        str(extracted.get(key) or "")
        for key in ("vendor", "vendor_name", "contractor", "problem", "reply")
    )
    return _match_vendor(blob, vendors) if blob.strip() else None


def _amount_from_extraction(extracted: dict[str, Any]) -> Decimal | None:
    value = extracted.get("amount")
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        amount = Decimal(str(value))
        return amount if amount > 0 else None
    return _match_amount(str(value)) or _match_amount(f"${value}")


def _date_from_extraction(extracted: dict[str, Any], today: date) -> str:
    value = str(extracted.get("date") or "").strip().lower()
    if not value:
        return ""
    return _match_date(value, today) or (_match_date(f" {value} ", today))

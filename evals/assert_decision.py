import json


def get_assert(output, context):
    data = json.loads(output) if isinstance(output, str) else output
    expected = context["vars"]["expected_decision"]
    actual = data.get("decision")
    tools = data.get("tools") or []
    passed = actual == expected
    reason = f"decision={actual} expected={expected} tools={tools}"
    if expected == "approved" and "process_payment" not in tools:
        passed = False
        reason += " missing process_payment"
    if expected in {"rejected", "needs_review"} and "process_payment" in tools:
        passed = False
        reason += " process_payment must not run"
    return {
        "pass": passed,
        "score": 1 if passed else 0,
        "reason": reason,
    }

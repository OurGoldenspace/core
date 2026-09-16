import json


def get_assert(output, context):
    data = json.loads(output) if isinstance(output, str) else output
    expected = context["vars"]["expected_decision"]
    actual = data.get("decision")
    tools = data.get("tools") or []
    passed = actual == expected
    reason = f"decision={actual} expected={expected} tools={tools}"
    if expected == "approved" and "create_work_order" not in tools:
        passed = False
        reason += " missing create_work_order"
    if expected in {"rejected", "needs_review"} and "create_work_order" in tools:
        passed = False
        reason += " create_work_order must not run"
    return {
        "pass": passed,
        "score": 1 if passed else 0,
        "reason": reason,
    }

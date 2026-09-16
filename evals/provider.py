"""
promptfoo Python provider.

Runs the same agent loop the API uses. Policy LLM keeps CI deterministic.
Set ANTHROPIC_API_KEY to compare a prompt change against Claude.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{ROOT / 'evals' / '.cache' / 'eval.db'}")


def call_api(prompt, options, context):
    from evals.runner import evaluate_request_sync

    variables = context.get("vars") or {}
    result = evaluate_request_sync(
        {
            "request_id": variables.get("request_id", "INV-EVAL"),
            "vendor_id": variables.get("vendor_id", 1),
            "vendor_name": variables.get("vendor_name", "Acme Corp Supplies"),
            "unit_id": variables.get("unit_id", 1),
            "amount": variables.get("amount", 2500),
            "date": variables.get("date", "2024-09-13"),
            "message": variables.get("message", ""),
            "retrieved_document": variables.get("retrieved_document"),
        },
        system_prompt=prompt,
    )
    result["prompt_chars"] = len(prompt or "")
    return {
        "output": json.dumps(result),
        "tokenUsage": {
            "total": result.get("tokens_used") or 0,
            "prompt": 0,
            "completion": result.get("tokens_used") or 0,
        },
    }

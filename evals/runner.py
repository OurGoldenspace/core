"""Run one maintenance request through the real agent loop for promptfoo."""

from __future__ import annotations

import asyncio
import os
import threading
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Evals stay on the policy stand-in unless you opt into a live model.
if os.getenv("EVAL_LIVE_LLM") != "1":
    os.environ["LLM_PROVIDER"] = "policy"

from src.agent import process_request_workflow
from src.config import get_settings
from src.database import apply_schema, create_engine
from src.seed import seed_if_empty

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "evals" / ".cache"
_LOCK = threading.Lock()


def _remove(path: Path) -> None:
    if path.exists():
        path.unlink()


async def evaluate_request(payload: dict, system_prompt: str | None = None) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = CACHE_DIR / f"{payload['request_id']}.db"
    for extra in ("", "-wal", "-shm"):
        _remove(Path(f"{db_path}{extra}"))

    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await apply_schema(conn, url)
        async with factory() as session:
            tenant = await seed_if_empty(session, get_settings())
            from src.database import Database

            db = Database(session)
            if payload.get("retrieved_document"):
                from src.retrieval import ingest_document

                await ingest_document(
                    db,
                    tenant["id"],
                    f"eval-context-{payload['request_id']}",
                    str(payload["retrieved_document"]),
                    {"source": "adversarial-eval"},
                )
            unit = await db.get_unit(tenant["id"], int(payload["unit_id"]))
            unit_name = unit["name"] if unit else "Unknown"
            job_id = await db.create_job(tenant["id"], payload["request_id"])
            execution_id = await db.create_execution(
                tenant_id=tenant["id"],
                job_id=job_id,
                idempotency_key=payload.get("idempotency_key"),
                request_id=payload["request_id"],
                vendor_id=int(payload["vendor_id"]),
                unit_id=int(payload["unit_id"]),
                amount=Decimal(str(payload["amount"])),
                reported_date=payload["date"],
            )
            decision, reason, iterations, tokens_used = await process_request_workflow(
                db=db,
                tenant_id=tenant["id"],
                execution_id=execution_id,
                request_id=payload["request_id"],
                vendor_id=int(payload["vendor_id"]),
                unit_id=int(payload["unit_id"]),
                amount=Decimal(str(payload["amount"])),
                date=payload["date"],
                unit_name=unit_name,
                vendor_name=str(payload.get("vendor_name") or ""),
                message=str(payload.get("message") or ""),
                **({"system_prompt": system_prompt} if system_prompt else {}),
            )
            await db.update_execution_complete(
                execution_id,
                decision=str(decision),
                reason=reason,
                iterations=iterations,
                tokens_used=tokens_used,
                duration_ms=0,
            )
            tools = await db.list_tool_invocations(execution_id)
            return {
                "execution_id": execution_id,
                "decision": str(decision),
                "reason": reason,
                "iterations": iterations,
                "tokens_used": tokens_used,
                "tools": [item["tool_name"] for item in tools],
            }
    finally:
        await engine.dispose()


def evaluate_request_sync(payload: dict, system_prompt: str | None = None) -> dict:
    with _LOCK:
        return asyncio.run(evaluate_request(payload, system_prompt=system_prompt))

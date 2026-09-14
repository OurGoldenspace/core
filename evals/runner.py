"""Run one invoice through the real agent loop for promptfoo."""

from __future__ import annotations

import asyncio
import threading
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agent import process_invoice_workflow
from src.config import get_settings
from src.database import apply_schema, create_engine
from src.seed import seed_if_empty

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "evals" / ".cache"
_LOCK = threading.Lock()


def _remove(path: Path) -> None:
    if path.exists():
        path.unlink()


async def evaluate_invoice(payload: dict, system_prompt: str | None = None) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not (ROOT / "data" / "vendors.json").exists():
        from data.generate_data import main as generate

        generate()

    db_path = CACHE_DIR / f"{payload['invoice_id']}.db"
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
                    f"eval-context-{payload['invoice_id']}",
                    str(payload["retrieved_document"]),
                    {"source": "adversarial-eval"},
                )
            department = await db.get_department(tenant["id"], int(payload["department_id"]))
            department_name = department["name"] if department else "Unknown"
            job_id = await db.create_job(tenant["id"], payload["invoice_id"])
            execution_id = await db.create_execution(
                tenant_id=tenant["id"],
                job_id=job_id,
                idempotency_key=payload.get("idempotency_key"),
                invoice_id=payload["invoice_id"],
                vendor_id=int(payload["vendor_id"]),
                department_id=int(payload["department_id"]),
                amount=Decimal(str(payload["amount"])),
                invoice_date=payload["date"],
            )
            decision, reason, iterations, tokens_used = await process_invoice_workflow(
                db=db,
                tenant_id=tenant["id"],
                execution_id=execution_id,
                invoice_id=payload["invoice_id"],
                vendor_id=int(payload["vendor_id"]),
                department_id=int(payload["department_id"]),
                amount=Decimal(str(payload["amount"])),
                date=payload["date"],
                department_name=department_name,
                vendor_name=str(payload.get("vendor_name") or ""),
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


def evaluate_invoice_sync(payload: dict, system_prompt: str | None = None) -> dict:
    with _LOCK:
        return asyncio.run(evaluate_invoice(payload, system_prompt=system_prompt))

"""The queue worker executes payloads, retries failures, and dead-letters."""

from __future__ import annotations

from sqlalchemy import text

from src.database import Database, get_session_factory
from src.worker import run_once

AUTH = {"Authorization": "Bearer test-key-12345"}


async def test_enqueued_invoice_is_processed_by_worker(client) -> None:
    payload = {
        "invoice_id": "INV-WORKER-APPROVE",
        "vendor_id": 1,
        "vendor_name": "Acme Corp Supplies",
        "department_id": 1,
        "amount": 777.00,
        "date": "2024-05-01",
        "idempotency_key": "worker-request-001",
    }
    queued = await client.post("/invoice-jobs", headers=AUTH, json=payload)
    assert queued.status_code == 202
    assert queued.json()["status"] == "pending"

    claimed_job_id = await run_once(worker_id=42)
    assert claimed_job_id == queued.json()["job_id"]

    completed = await client.get(
        f"/invoice-jobs/{claimed_job_id}",
        headers=AUTH,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["decision"] == "approved"

    audit = await client.get(
        f"/executions/{queued.json()['execution_id']}",
        headers=AUTH,
    )
    tool_names = [tool["tool_name"] for tool in audit.json()["tools"]]
    assert "process_payment" in tool_names


async def test_enqueued_high_amount_waits_for_human(client) -> None:
    queued = await client.post(
        "/invoice-jobs",
        headers=AUTH,
        json={
            "invoice_id": "INV-WORKER-REVIEW",
            "vendor_id": 1,
            "vendor_name": "Acme Corp Supplies",
            "department_id": 1,
            "amount": 7500.00,
            "date": "2024-05-02",
        },
    )
    await run_once(worker_id=43)
    pending = await client.get(
        f"/invoice-jobs/{queued.json()['job_id']}",
        headers=AUTH,
    )
    assert pending.json()["status"] == "awaiting_review"
    assert pending.json()["decision"] == "needs_review"


async def test_retry_backoff_eventually_dead_letters(seeded_session) -> None:
    db = Database(seeded_session)
    tenant = await db.get_tenant_by_api_key("test-key-12345")
    job_id = await db.create_job(tenant["id"], "INV-DEAD-LETTER")

    states = []
    for attempt in range(3):
        if attempt:
            await seeded_session.execute(
                text(
                    """
                    UPDATE jobs
                    SET available_at = '2000-01-01 00:00:00'
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
            await seeded_session.commit()
        assert await db.claim_job(tenant["id"], "INV-DEAD-LETTER", worker_id=7) == job_id
        states.append(
            await db.retry_or_dead_letter_job(
                job_id=job_id,
                worker_id=7,
                error=f"attempt {attempt + 1} failed",
                max_retries=3,
                backoff_seconds=2 ** attempt,
            )
        )

    assert states == ["pending", "pending", "dead_letter"]
    job = await db.get_job(job_id)
    assert job["retry_count"] == 3
    assert job["status"] == "dead_letter"
    assert job["last_error"] == "attempt 3 failed"


async def test_recent_heartbeat_prevents_reclaim(seeded_session) -> None:
    db = Database(seeded_session)
    tenant = await db.get_tenant_by_api_key("test-key-12345")
    job_id = await db.create_job(tenant["id"], "INV-HEARTBEAT")
    assert await db.claim_job(tenant["id"], "INV-HEARTBEAT", worker_id=8) == job_id
    assert await db.heartbeat_job(job_id, worker_id=8)
    assert await db.reclaim_stale_jobs(stale_seconds=60, max_retries=3) == 0

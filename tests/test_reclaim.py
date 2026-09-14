"""A worker that dies mid-claim does not lose the job forever."""

from __future__ import annotations

from sqlalchemy import text

from src.database import Database


async def test_stale_claim_is_returned_to_pending(seeded_session) -> None:
    db = Database(seeded_session)
    tenant = await db.get_tenant_by_api_key("test-key-12345")
    assert tenant is not None

    job_id = await db.create_job(tenant["id"], "INV-DEAD-WORKER")
    claimed = await db.claim_job(tenant["id"], "INV-DEAD-WORKER", worker_id=99)
    assert claimed == job_id

    await seeded_session.execute(
        text("UPDATE jobs SET claimed_at = '2000-01-01 00:00:00' WHERE id = :job_id"),
        {"job_id": job_id},
    )
    await seeded_session.commit()

    reclaimed = await db.reclaim_stale_jobs(stale_seconds=1, max_retries=3)
    assert reclaimed == 1

    job = await db.get_job(job_id)
    assert job is not None
    assert job["status"] == "pending"
    assert job["claimed_by"] is None
    assert job["retry_count"] == 1

    second = await db.claim_next_job(worker_id=2)
    assert second == job_id

"""Durable background worker with heartbeat, retry, and dead-letter handling."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from src.agent import process_request_workflow
from src.config import settings
from src.database import Database, get_session_factory, init_db

logger = logging.getLogger(__name__)


async def reclaim_and_claim(worker_id: int) -> int | None:
    factory = get_session_factory()
    async with factory() as session:
        db = Database(session)
        reclaimed = await db.reclaim_stale_jobs(
            stale_seconds=settings.CLAIM_STALE_SECONDS,
            max_retries=settings.MAX_JOB_RETRIES,
        )
        if reclaimed:
            logger.info("Reclaimed %s stale job(s)", reclaimed)
        return await db.claim_next_job(worker_id)


async def heartbeat(job_id: int, worker_id: int, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=settings.JOB_HEARTBEAT_SECONDS)
            return
        except asyncio.TimeoutError:
            factory = get_session_factory()
            async with factory() as session:
                is_owned = await Database(session).heartbeat_job(job_id, worker_id)
            if not is_owned:
                logger.warning("Worker %s lost job %s", worker_id, job_id)
                return


async def process_claimed_job(job_id: int, worker_id: int) -> str:
    stopped = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat(job_id, worker_id, stopped))
    try:
        factory = get_session_factory()
        async with factory() as session:
            db = Database(session)
            job = await db.get_job(job_id)
            if job is None or job["status"] != "claimed" or job["claimed_by"] != worker_id:
                raise RuntimeError(f"Worker {worker_id} does not own job {job_id}")

            execution = await db.get_execution_by_job(job_id)
            if execution is None:
                raise RuntimeError(f"Job {job_id} has no execution payload")

            vendor = await db.get_vendor(job["tenant_id"], execution["vendor_id"])
            unit = await db.get_unit(job["tenant_id"], execution["unit_id"])
            vendor_name = vendor["name"] if vendor else f"Vendor {execution['vendor_id']}"
            unit_name = unit["name"] if unit else f"Unit {execution['unit_id']}"
            decision, reason, iterations, tokens_used = await process_request_workflow(
                db=db,
                tenant_id=job["tenant_id"],
                execution_id=execution["id"],
                request_id=execution["request_id"],
                vendor_id=execution["vendor_id"],
                unit_id=execution["unit_id"],
                amount=execution["amount"],
                date=execution["reported_date"],
                unit_name=unit_name,
                vendor_name=vendor_name,
            )

            if decision == "error":
                backoff_seconds = settings.JOB_RETRY_BASE_SECONDS * (
                    2 ** int(job["retry_count"])
                )
                next_state = await db.retry_or_dead_letter_job(
                    job_id=job_id,
                    worker_id=worker_id,
                    error=reason,
                    max_retries=settings.MAX_JOB_RETRIES,
                    backoff_seconds=backoff_seconds,
                )
                if next_state == "dead_letter":
                    await db.update_execution_complete(
                        execution["id"],
                        decision="error",
                        reason=reason,
                        iterations=iterations,
                        tokens_used=tokens_used,
                        duration_ms=0,
                    )
                return next_state

            await db.update_execution_complete(
                execution["id"],
                decision=decision,
                reason=reason,
                iterations=iterations,
                tokens_used=tokens_used,
                duration_ms=0,
            )
            if decision == "needs_review":
                await db.set_job_awaiting_review(job_id)
                return "awaiting_review"

            await db.complete_job(job_id)
            return "completed"
    except Exception as error:
        logger.exception("Worker %s failed job %s", worker_id, job_id)
        factory = get_session_factory()
        async with factory() as session:
            db = Database(session)
            job = await db.get_job(job_id)
            retry_count = int(job["retry_count"]) if job else 0
            return await db.retry_or_dead_letter_job(
                job_id=job_id,
                worker_id=worker_id,
                error=str(error),
                max_retries=settings.MAX_JOB_RETRIES,
                backoff_seconds=settings.JOB_RETRY_BASE_SECONDS * (2 ** retry_count),
            )
    finally:
        stopped.set()
        await heartbeat_task


async def run_once(worker_id: int) -> int | None:
    job_id = await reclaim_and_claim(worker_id)
    if job_id is None:
        return None
    state = await process_claimed_job(job_id, worker_id)
    logger.info("Worker %s finished job %s with state %s", worker_id, job_id, state)
    return job_id


async def run_forever(worker_id: int, poll_seconds: float = 1.0) -> None:
    await init_db()
    logger.info("Worker %s polling for jobs", worker_id)
    while True:
        job_id = await run_once(worker_id)
        if job_id is None:
            await asyncio.sleep(poll_seconds)
            continue
        logger.info("Worker %s processed job %s", worker_id, job_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Claim maintenance jobs")
    parser.add_argument("--worker-id", type=int, default=int(os.getenv("WORKER_ID", "1")))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        job_id = asyncio.run(run_once(args.worker_id))
        print(job_id)
        return
    asyncio.run(run_forever(args.worker_id))


if __name__ == "__main__":
    main()

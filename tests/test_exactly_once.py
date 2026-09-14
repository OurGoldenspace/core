"""
Exactly-once: unique constraint + atomic claim.

50 workers race 20 pending jobs. Each job is claimed once.
The same idempotency key never creates a second execution.
"""

from __future__ import annotations

import asyncio

import pytest
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.database import Database, get_session_factory


AUTH = {"Authorization": "Bearer test-key-12345"}


@pytest.mark.asyncio
async def test_idempotency_key_prevents_duplicates(client) -> None:
    payload = {
        "invoice_id": "INV-2024-IDEM-1",
        "vendor_id": 1,
        "vendor_name": "Acme Corp Supplies",
        "department_id": 1,
        "amount": 2500.00,
        "date": "2024-09-13",
        "idempotency_key": "abc123",
    }
    first = await client.post("/process-invoice", headers=AUTH, json=payload)
    second = await client.post("/process-invoice", headers=AUTH, json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["execution_id"] == second.json()["execution_id"]
    assert second.json()["cached"] is True


@pytest.mark.asyncio
async def test_concurrent_retries_execute_and_pay_once(client) -> None:
    payload = {
        "invoice_id": "INV-CONCURRENT-IDEMPOTENCY",
        "vendor_id": 1,
        "vendor_name": "Acme Corp Supplies",
        "department_id": 1,
        "amount": 321.45,
        "date": "2024-09-13",
        "idempotency_key": "concurrent-key-001",
    }

    responses = await asyncio.gather(
        *[
            client.post("/process-invoice", headers=AUTH, json=payload)
            for _ in range(10)
        ]
    )

    assert {response.status_code for response in responses} == {200}
    execution_ids = {response.json()["execution_id"] for response in responses}
    assert len(execution_ids) == 1
    assert sum(not response.json()["cached"] for response in responses) == 1

    execution_id = execution_ids.pop()
    audit = await client.get(f"/executions/{execution_id}", headers=AUTH)
    tool_names = [tool["tool_name"] for tool in audit.json()["tools"]]
    assert tool_names.count("process_payment") == 1

    factory = get_session_factory()
    async with factory() as session:
        db = Database(session)
        tenant = await db.get_tenant_by_api_key("test-key-12345")
        payments = await db.list_payments(tenant["id"], payload["invoice_id"])
    assert len(payments) == 1
    assert payments[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_same_invoice_with_different_keys_still_executes_once(client) -> None:
    base = {
        "invoice_id": "INV-TWO-REQUEST-KEYS",
        "vendor_id": 1,
        "vendor_name": "Acme Corp Supplies",
        "department_id": 1,
        "amount": 654.32,
        "date": "2024-09-13",
    }
    first, second = await asyncio.gather(
        client.post(
            "/process-invoice",
            headers=AUTH,
            json={**base, "idempotency_key": "request-key-a"},
        ),
        client.post(
            "/process-invoice",
            headers=AUTH,
            json={**base, "idempotency_key": "request-key-b"},
        ),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["execution_id"] == second.json()["execution_id"]


@pytest.mark.asyncio
async def test_reusing_key_for_different_payload_is_conflict(client) -> None:
    base = {
        "invoice_id": "INV-IDEM-PAYLOAD-A",
        "vendor_id": 1,
        "vendor_name": "Acme Corp Supplies",
        "department_id": 1,
        "amount": 100.00,
        "date": "2024-09-13",
        "idempotency_key": "payload-bound-key",
    }
    first = await client.post("/process-invoice", headers=AUTH, json=base)
    second = await client.post(
        "/process-invoice",
        headers=AUTH,
        json={**base, "invoice_id": "INV-IDEM-PAYLOAD-B", "amount": 200.00},
    )
    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_concurrent_workers_claiming_jobs(seeded_session) -> None:
    db = Database(seeded_session)
    tenant = await db.get_tenant_by_api_key("test-key-12345")
    assert tenant is not None

    for index in range(20):
        await db.create_job(tenant["id"], f"INV-CLAIM-{index:02d}")

    async def worker(worker_id: int) -> int | None:
        factory = get_session_factory()
        async with factory() as session:
            return await Database(session).claim_next_job(worker_id)

    claimed = await asyncio.gather(*[worker(index) for index in range(50)])
    successful = [job_id for job_id in claimed if job_id is not None]
    assert len(successful) == 20
    assert len(set(successful)) == 20


@pytest.mark.asyncio
async def test_database_unique_constraint_idempotency(seeded_session) -> None:
    db = Database(seeded_session)
    tenant = await db.get_tenant_by_api_key("test-key-12345")
    assert tenant is not None
    job_id = await db.create_job(tenant["id"], "INV-UNIQUE-CONSTRAINT")
    first = await db.create_execution(
        tenant_id=tenant["id"],
        job_id=job_id,
        idempotency_key="same-key",
        invoice_id="INV-UNIQUE-CONSTRAINT",
        vendor_id=1,
        department_id=1,
        amount=Decimal("100.00"),
    )
    second = await db.create_execution(
        tenant_id=tenant["id"],
        job_id=job_id,
        idempotency_key="same-key",
        invoice_id="INV-UNIQUE-CONSTRAINT",
        vendor_id=1,
        department_id=1,
        amount=Decimal("100.00"),
    )
    assert first == second

    with pytest.raises(IntegrityError):
        await seeded_session.execute(
            text(
                """
                INSERT INTO executions (
                    tenant_id, job_id, idempotency_key, invoice_id,
                    vendor_id, department_id, amount
                )
                VALUES (:tenant_id, :job_id, :key, :invoice_id, 1, 1, 100)
                """
            ),
            {
                "tenant_id": tenant["id"],
                "job_id": job_id,
                "key": "same-key",
                "invoice_id": "INV-UNIQUE-CONSTRAINT-2",
            },
        )
        await seeded_session.commit()

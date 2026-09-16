"""A person stays in control of anything at or above $5000."""

from __future__ import annotations

import asyncio

from src.database import Database, get_session_factory

AUTH = {"Authorization": "Bearer test-key-12345"}

REVIEW_REQUEST = {
    "request_id": "INV-HITL-7500",
    "vendor_id": 1,
    "vendor_name": "Acme Corp Supplies",
    "unit_id": 1,
    "amount": 7500.00,
    "date": "2024-09-13",
}


async def test_human_can_approve_review_queue(client) -> None:
    created = await client.post("/process-request", headers=AUTH, json=REVIEW_REQUEST)
    assert created.json()["decision"] == "needs_review"
    execution_id = created.json()["execution_id"]
    pending = await client.get(f"/executions/{execution_id}", headers=AUTH)
    assert pending.json()["state"] == "awaiting_review"

    approved = await client.post(
        f"/executions/{execution_id}/approve",
        headers=AUTH,
        json={"reviewer": "finance-ops", "note": "Confirmed with department head"},
    )
    assert approved.status_code == 200
    assert approved.json()["decision"] == "approved"

    audit = await client.get(f"/executions/{execution_id}", headers=AUTH)
    tools = [item["tool_name"] for item in audit.json()["tools"]]
    assert "human_approve" in tools
    assert audit.json()["decision"] == "approved"
    assert audit.json()["state"] == "completed"

    factory = get_session_factory()
    async with factory() as session:
        db = Database(session)
        job = await db.get_job(audit.json()["job_id"])
        tenant = await db.get_tenant_by_api_key("test-key-12345")
        payments = await db.list_work_orders(tenant["id"], REVIEW_REQUEST["request_id"])
    assert job["status"] == "completed"
    assert len(payments) == 1


async def test_human_can_reject_review_queue(client) -> None:
    created = await client.post(
        "/process-request",
        headers=AUTH,
        json={**REVIEW_REQUEST, "request_id": "INV-HITL-REJECT"},
    )
    execution_id = created.json()["execution_id"]
    rejected = await client.post(
        f"/executions/{execution_id}/reject",
        headers=AUTH,
        json={"reviewer": "finance-ops", "note": "Duplicate of last quarter"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["decision"] == "rejected"


async def test_cannot_approve_already_decided(client) -> None:
    created = await client.post(
        "/process-request",
        headers=AUTH,
        json={
            "request_id": "INV-HITL-ALREADY",
            "vendor_id": 1,
            "vendor_name": "Acme Corp Supplies",
            "unit_id": 1,
            "amount": 2500.00,
            "date": "2024-09-13",
        },
    )
    assert created.json()["decision"] == "approved"
    response = await client.post(
        f"/executions/{created.json()['execution_id']}/approve",
        headers=AUTH,
        json={"reviewer": "finance-ops"},
    )
    assert response.status_code == 409


async def test_two_human_approvers_create_one_payment(client) -> None:
    created = await client.post(
        "/process-request",
        headers=AUTH,
        json={**REVIEW_REQUEST, "request_id": "INV-HITL-RACE"},
    )
    execution_id = created.json()["execution_id"]

    responses = await asyncio.gather(
        *[
            client.post(
                f"/executions/{execution_id}/approve",
                headers=AUTH,
                json={"reviewer": f"reviewer-{index}"},
            )
            for index in range(2)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]

    factory = get_session_factory()
    async with factory() as session:
        db = Database(session)
        tenant = await db.get_tenant_by_api_key("test-key-12345")
        payments = await db.list_work_orders(tenant["id"], "INV-HITL-RACE")
    assert len(payments) == 1
    assert payments[0]["status"] == "succeeded"

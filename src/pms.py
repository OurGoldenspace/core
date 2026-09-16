"""
AppFolio/Yardi/Buildium-shaped adapter.

This demo does not call a live PMS. The interface is the production boundary:
lookup a unit, then create one work order keyed for replay.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from src.database import Database


class PropertyManagementClient:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def lookup_unit(
        self,
        tenant_id: int,
        unit_id: int,
        estimated_cost: Decimal,
    ) -> dict[str, Any]:
        unit = await self.db.get_unit(tenant_id, unit_id)
        await asyncio.sleep(0.05)
        if unit is None:
            return {
                "found": False,
                "has_budget": False,
                "available": Decimal("0"),
                "required": estimated_cost,
                "reason": f"Unit {unit_id} was not found in the PMS",
            }

        available = unit["budget_available"]
        has_budget = available >= estimated_cost
        label = f"{unit['name']} at {unit.get('property_name') or 'property'}"
        if has_budget:
            reason = f"{label} has owner cap ${available} for a ${estimated_cost} job"
        else:
            reason = (
                f"{label} owner cap is ${available}, below the ${estimated_cost} estimate"
            )
        return {
            "found": True,
            "has_budget": has_budget,
            "available": available,
            "required": estimated_cost,
            "unit_name": unit["name"],
            "property_name": unit.get("property_name"),
            "reason": reason,
        }

    async def create_work_order(
        self,
        tenant_id: int,
        execution_id: int,
        request_id: str,
        vendor_id: int,
        estimated_cost: Decimal,
    ) -> dict[str, Any]:
        work_order, is_owner = await self.db.reserve_work_order(
            tenant_id=tenant_id,
            execution_id=execution_id,
            request_id=request_id,
            vendor_id=vendor_id,
            amount=estimated_cost,
        )
        await asyncio.sleep(0.1)
        await self.db.complete_work_order(work_order["id"])
        return {
            "success": True,
            "transaction_id": work_order["transaction_id"],
            "idempotency_key": work_order["idempotency_key"],
            "was_replayed": not is_owner,
            "reason": (
                f"Created PMS work order {work_order['transaction_id']} "
                f"for request {request_id}"
            ),
        }

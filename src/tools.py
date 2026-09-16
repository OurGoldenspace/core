"""
Maintenance tools.

1. validate_vendor - contractor is approved and not high-risk
2. lookup_unit - unit exists in the PMS and owner cap covers the estimate
3. detect_open_work_orders - same unit, estimate, and date already in flight
4. create_work_order - one PMS write, replay-safe
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from src.database import Database
from src.models import (
    CreateWorkOrderInput,
    CreateWorkOrderOutput,
    DetectOpenWorkOrdersInput,
    DetectOpenWorkOrdersOutput,
    LookupUnitInput,
    LookupUnitOutput,
    ValidateVendorInput,
    ValidateVendorOutput,
)
from src.pms import PropertyManagementClient

logger = logging.getLogger(__name__)


async def validate_vendor(
    db: Database,
    tenant_id: int,
    vendor_id: int,
) -> dict[str, Any]:
    vendor = await db.get_vendor(tenant_id, vendor_id)
    await asyncio.sleep(0.05)
    if not vendor:
        return {
            "is_approved": False,
            "risk_level": "unknown",
            "reason": f"Vendor {vendor_id} not found in the PMS",
        }
    if not vendor["is_approved"]:
        return {
            "is_approved": False,
            "risk_level": vendor["risk_level"],
            "reason": f"Vendor {vendor['name']} is not an approved contractor",
        }
    if vendor["risk_level"] == "high":
        return {
            "is_approved": False,
            "risk_level": vendor["risk_level"],
            "reason": f"Vendor {vendor['name']} is high-risk",
        }
    return {
        "is_approved": True,
        "risk_level": vendor["risk_level"],
        "reason": f"Vendor {vendor['name']} is approved and {vendor['risk_level']}-risk",
    }


async def lookup_unit(
    db: Database,
    tenant_id: int,
    unit_id: int,
    amount: Decimal,
) -> dict[str, Any]:
    return await PropertyManagementClient(db).lookup_unit(tenant_id, unit_id, amount)


async def detect_open_work_orders(
    db: Database,
    tenant_id: int,
    unit_id: int,
    amount: Decimal,
    date: str,
) -> dict[str, Any]:
    await asyncio.sleep(0.05)
    matching = await db.find_open_work_orders(tenant_id, unit_id, amount, date)
    if matching:
        return {
            "is_duplicate": True,
            "matching_requests": matching,
            "reason": f"Found {len(matching)} duplicate open request(s) for the same unit, estimate, and date",
        }
    return {
        "is_duplicate": False,
        "matching_requests": [],
        "reason": "No open work order for this unit, estimate, and date",
    }


async def create_work_order(
    db: Database,
    tenant_id: int,
    execution_id: int,
    request_id: str,
    vendor_id: int,
    amount: Decimal,
) -> dict[str, Any]:
    result = await PropertyManagementClient(db).create_work_order(
        tenant_id=tenant_id,
        execution_id=execution_id,
        request_id=request_id,
        vendor_id=vendor_id,
        estimated_cost=amount,
    )
    logger.info("Created work order: %s -> %s", request_id, result["transaction_id"])
    return result


AVAILABLE_TOOLS = {
    "validate_vendor": {
        "description": "Validate if a contractor is approved and check risk level",
        "handler": validate_vendor,
        "parameters": {
            "vendor_id": {
                "type": "integer",
                "description": "PMS vendor id of the contractor",
            }
        },
    },
    "lookup_unit": {
        "description": "Look up the unit in the PMS and check the owner spend cap",
        "handler": lookup_unit,
        "parameters": {
            "unit_id": {
                "type": "integer",
                "description": "PMS unit id",
            },
            "amount": {
                "type": "number",
                "description": "Estimated repair cost",
            },
        },
    },
    "detect_open_work_orders": {
        "description": "Detect an open work order for the same unit, estimate, and date",
        "handler": detect_open_work_orders,
        "parameters": {
            "unit_id": {
                "type": "integer",
                "description": "PMS unit id",
            },
            "amount": {
                "type": "number",
                "description": "Estimated repair cost",
            },
            "date": {
                "type": "string",
                "description": "Reported date in YYYY-MM-DD format",
            },
        },
    },
    "create_work_order": {
        "description": "Create a work order in the PMS for an approved request",
        "handler": create_work_order,
        "parameters": {
            "request_id": {
                "type": "string",
                "description": "Maintenance request id",
            },
            "vendor_id": {
                "type": "integer",
                "description": "PMS vendor id of the contractor",
            },
            "amount": {
                "type": "number",
                "description": "Estimated repair cost",
            },
        },
    },
}


def get_tool_definitions_for_llm() -> list[dict]:
    return [
        {
            "name": name,
            "description": tool["description"],
            "input_schema": {
                "type": "object",
                "properties": {
                    param_name: {
                        "type": param["type"],
                        "description": param["description"],
                    }
                    for param_name, param in tool["parameters"].items()
                },
                "required": list(tool["parameters"].keys()),
            },
        }
        for name, tool in AVAILABLE_TOOLS.items()
    ]


async def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    db: Database,
    tenant_id: int,
    execution_id: int | None = None,
) -> tuple[bool, Any]:
    if tool_name not in AVAILABLE_TOOLS:
        return False, {"error": f"Tool {tool_name} not found"}

    tool = AVAILABLE_TOOLS[tool_name]
    handler = tool["handler"]

    try:
        if tool_name == "validate_vendor":
            payload = ValidateVendorInput.model_validate(tool_input)
            result = await handler(db, tenant_id, payload.vendor_id)
            result = ValidateVendorOutput.model_validate(result).model_dump(mode="json")
        elif tool_name == "lookup_unit":
            payload = LookupUnitInput.model_validate(tool_input)
            result = await handler(db, tenant_id, payload.unit_id, payload.amount)
            result = LookupUnitOutput.model_validate(result).model_dump(mode="json")
        elif tool_name == "detect_open_work_orders":
            payload = DetectOpenWorkOrdersInput.model_validate(tool_input)
            result = await handler(
                db, tenant_id, payload.unit_id, payload.amount, payload.date
            )
            result = DetectOpenWorkOrdersOutput.model_validate(result).model_dump(mode="json")
        elif tool_name == "create_work_order":
            if execution_id is None:
                return False, {"error": "execution_context_required"}
            payload = CreateWorkOrderInput.model_validate(tool_input)
            result = await handler(
                db,
                tenant_id,
                execution_id,
                payload.request_id,
                payload.vendor_id,
                payload.amount,
            )
            result = CreateWorkOrderOutput.model_validate(result).model_dump(mode="json")
        else:
            return False, {"error": f"Unknown tool: {tool_name}"}

        return True, result
    except ValidationError as error:
        logger.warning("Tool validation failed: %s: %s", tool_name, error)
        return False, {"error": "validation_failed", "details": error.errors()}
    except Exception as error:
        logger.error("Tool execution error: %s: %s", tool_name, error)
        return False, {"error": str(error)}

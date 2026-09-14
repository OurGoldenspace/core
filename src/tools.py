"""
Tool Functions for Invoice Processing

Tools available to the agent:
1. validate_vendor() - Check if vendor is approved and low risk
2. check_budget() - Verify department has available budget
3. detect_duplicates() - Find duplicate invoices (same vendor, amount, date)
4. process_payment() - Execute payment for approved invoices

Each tool is called by the agent based on LLM reasoning.
Inputs are validated with Pydantic before execution.
Outputs are logged for audit trail.
"""

import logging
import asyncio
from typing import Dict, Any
from decimal import Decimal

from pydantic import ValidationError

from src.models import (
    CheckBudgetOutput,
    CheckBudgetInput,
    DetectDuplicatesOutput,
    DetectDuplicatesInput,
    ProcessPaymentInput,
    ProcessPaymentOutput,
    ValidateVendorOutput,
    ValidateVendorInput,
)
from src.database import Database

logger = logging.getLogger(__name__)


# =========================================================================
# TOOL 1: VALIDATE VENDOR
# =========================================================================

async def validate_vendor(
    db: Database,
    tenant_id: int,
    vendor_id: int
) -> Dict[str, Any]:
    """
    Validate vendor: Is it approved? What's the risk level?
    
    Returns:
    {
        "is_approved": bool,
        "risk_level": str,
        "reason": str
    }
    """
    vendor = await db.get_vendor(tenant_id, vendor_id)
    
    if not vendor:
        return {
            "is_approved": False,
            "risk_level": "unknown",
            "reason": f"Vendor {vendor_id} not found in system"
        }
    
    # Simulate slight processing time (tools should be realistic)
    await asyncio.sleep(0.05)
    
    if not vendor["is_approved"]:
        return {
            "is_approved": False,
            "risk_level": vendor["risk_level"],
            "reason": f"Vendor {vendor['name']} is not approved"
        }
    
    if vendor["risk_level"] == "high":
        return {
            "is_approved": False,
            "risk_level": vendor["risk_level"],
            "reason": f"Vendor {vendor['name']} is high-risk"
        }
    
    return {
        "is_approved": True,
        "risk_level": vendor["risk_level"],
        "reason": f"Vendor {vendor['name']} is approved and {vendor['risk_level']}-risk"
    }


# =========================================================================
# TOOL 2: CHECK BUDGET
# =========================================================================

async def check_budget(
    db: Database,
    tenant_id: int,
    department_id: int,
    amount: Decimal
) -> Dict[str, Any]:
    """
    Check budget: Does department have enough available budget?
    
    Returns:
    {
        "has_budget": bool,
        "available": Decimal,
        "required": Decimal,
        "reason": str
    }
    """
    department = await db.get_department(tenant_id, department_id)
    
    if not department:
        return {
            "has_budget": False,
            "available": Decimal("0"),
            "required": amount,
            "reason": f"Department {department_id} not found"
        }
    
    # Simulate processing time
    await asyncio.sleep(0.05)
    
    available = department["budget_available"]
    
    if available >= amount:
        return {
            "has_budget": True,
            "available": available,
            "required": amount,
            "reason": f"Department {department['name']} has sufficient budget. Available: ${available}"
        }
    else:
        return {
            "has_budget": False,
            "available": available,
            "required": amount,
            "reason": f"Department {department['name']} insufficient budget. Available: ${available}, Required: ${amount}"
        }


# =========================================================================
# TOOL 3: DETECT DUPLICATES
# =========================================================================

async def detect_duplicates(
    db: Database,
    tenant_id: int,
    vendor_id: int,
    amount: Decimal,
    date: str
) -> Dict[str, Any]:
    """
    Detect duplicates: Are there other invoices with same vendor, amount, date?
    
    Returns:
    {
        "is_duplicate": bool,
        "matching_invoices": [list of invoice IDs],
        "reason": str
    }
    """
    # Simulate processing time
    await asyncio.sleep(0.05)
    
    matching_invoices = await db.find_duplicate_invoices(
        tenant_id, vendor_id, amount, date
    )
    
    if matching_invoices:
        return {
            "is_duplicate": True,
            "matching_invoices": matching_invoices,
            "reason": f"Found {len(matching_invoices)} invoice(s) with same vendor, amount, and date"
        }
    else:
        return {
            "is_duplicate": False,
            "matching_invoices": [],
            "reason": "No duplicate invoices found"
        }


# =========================================================================
# TOOL 4: PROCESS PAYMENT
# =========================================================================

async def process_payment(
    db: Database,
    tenant_id: int,
    execution_id: int,
    invoice_id: str,
    vendor_id: int,
    amount: Decimal
) -> Dict[str, Any]:
    """
    Process payment: Mark invoice as approved and generate transaction.
    
    Returns:
    {
        "success": bool,
        "transaction_id": Optional[str],
        "reason": str
    }
    """
    payment, is_owner = await db.reserve_payment(
        tenant_id=tenant_id,
        execution_id=execution_id,
        invoice_id=invoice_id,
        vendor_id=vendor_id,
        amount=amount,
    )

    # A real provider receives payment["idempotency_key"]. Replaying that key
    # after a timeout returns the same provider-side transaction.
    await asyncio.sleep(0.1)
    await db.complete_payment(payment["id"])
    transaction_id = payment["transaction_id"]

    logger.info("Processed payment: %s -> %s", invoice_id, transaction_id)
    
    return {
        "success": True,
        "transaction_id": transaction_id,
        "idempotency_key": payment["idempotency_key"],
        "was_replayed": not is_owner,
        "reason": f"Successfully processed payment of ${amount} for invoice {invoice_id}",
    }


# =========================================================================
# TOOL REGISTRY
# =========================================================================

AVAILABLE_TOOLS = {
    "validate_vendor": {
        "description": "Validate if a vendor is approved and check risk level",
        "handler": validate_vendor,
        "parameters": {
            "vendor_id": {
                "type": "integer",
                "description": "ID of the vendor to validate"
            }
        }
    },
    "check_budget": {
        "description": "Check if department has available budget for an invoice amount",
        "handler": check_budget,
        "parameters": {
            "department_id": {
                "type": "integer",
                "description": "ID of the department"
            },
            "amount": {
                "type": "number",
                "description": "Invoice amount to check"
            }
        }
    },
    "detect_duplicates": {
        "description": "Detect if invoice is a duplicate (same vendor, amount, date)",
        "handler": detect_duplicates,
        "parameters": {
            "vendor_id": {
                "type": "integer",
                "description": "ID of the vendor"
            },
            "amount": {
                "type": "number",
                "description": "Invoice amount"
            },
            "date": {
                "type": "string",
                "description": "Invoice date in YYYY-MM-DD format"
            }
        }
    },
    "process_payment": {
        "description": "Process payment for an approved invoice",
        "handler": process_payment,
        "parameters": {
            "invoice_id": {
                "type": "string",
                "description": "ID of the invoice"
            },
            "vendor_id": {
                "type": "integer",
                "description": "ID of the vendor"
            },
            "amount": {
                "type": "number",
                "description": "Invoice amount"
            }
        }
    }
}


def get_tool_definitions_for_llm() -> list[dict]:
    """
    Get tool definitions formatted for LLM.
    This is what we send to Claude so it knows what tools are available.
    """
    return [
        {
            "name": name,
            "description": tool["description"],
            "input_schema": {
                "type": "object",
                "properties": {
                    param_name: {
                        "type": param["type"],
                        "description": param["description"]
                    }
                    for param_name, param in tool["parameters"].items()
                },
                "required": list(tool["parameters"].keys())
            }
        }
        for name, tool in AVAILABLE_TOOLS.items()
    ]


async def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    db: Database,
    tenant_id: int,
    execution_id: int | None = None,
) -> tuple[bool, Any]:
    """
    Execute a tool with given input.
    
    Returns:
    (success: bool, result: dict)
    
    success=False means validation error (didn't execute)
    success=True means tool executed (even if business logic rejected)
    """
    
    if tool_name not in AVAILABLE_TOOLS:
        return False, {"error": f"Tool {tool_name} not found"}

    tool = AVAILABLE_TOOLS[tool_name]
    handler = tool["handler"]

    try:
        if tool_name == "validate_vendor":
            payload = ValidateVendorInput.model_validate(tool_input)
            result = await handler(db, tenant_id, payload.vendor_id)
            result = ValidateVendorOutput.model_validate(result).model_dump(mode="json")
        elif tool_name == "check_budget":
            payload = CheckBudgetInput.model_validate(tool_input)
            result = await handler(db, tenant_id, payload.department_id, payload.amount)
            result = CheckBudgetOutput.model_validate(result).model_dump(mode="json")
        elif tool_name == "detect_duplicates":
            payload = DetectDuplicatesInput.model_validate(tool_input)
            result = await handler(
                db, tenant_id, payload.vendor_id, payload.amount, payload.date
            )
            result = DetectDuplicatesOutput.model_validate(result).model_dump(mode="json")
        elif tool_name == "process_payment":
            if execution_id is None:
                return False, {"error": "execution_context_required"}
            payload = ProcessPaymentInput.model_validate(tool_input)
            result = await handler(
                db,
                tenant_id,
                execution_id,
                payload.invoice_id,
                payload.vendor_id,
                payload.amount,
            )
            result = ProcessPaymentOutput.model_validate(result).model_dump(mode="json")
        else:
            return False, {"error": f"Unknown tool: {tool_name}"}

        return True, result
    except ValidationError as error:
        logger.warning(f"Tool validation failed: {tool_name}: {error}")
        return False, {"error": "validation_failed", "details": error.errors()}
    except Exception as e:
        logger.error(f"Tool execution error: {tool_name}: {str(e)}")
        return False, {"error": str(e)}
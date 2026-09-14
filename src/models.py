"""
Pydantic models for type-safe validation.

Every LLM tool call and HTTP payload is validated before it reaches SQL.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class InvoiceDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"
    ERROR = "error"


class AgentDecisionOutput(BaseModel):
    decision: Literal["approved", "rejected", "needs_review"]
    reason: str = Field(..., min_length=1, max_length=500)


class ProcessInvoiceRequest(BaseModel):
    invoice_id: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    vendor_id: int = Field(..., gt=0, lt=1_000_000)
    vendor_name: str = Field(..., min_length=1, max_length=255)
    department_id: int = Field(..., gt=0, lt=1_000)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    date: str
    idempotency_key: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if value > Decimal("999999.99"):
            raise ValueError("amount too large (max $999,999.99)")
        return value

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")
        return value


class ProcessInvoiceResponse(BaseModel):
    execution_id: int
    invoice_id: str
    decision: str
    reason: str
    iterations: int
    tokens_used: int
    duration_ms: int
    cached: bool = False


class HumanReviewRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=80)
    note: Optional[str] = None


class HumanReviewResponse(BaseModel):
    execution_id: int
    decision: str
    reason: str
    reviewer: str


class BackgroundJobResponse(BaseModel):
    job_id: int
    execution_id: int
    status: str
    decision: Optional[str] = None
    cached: bool = False


class IngestDocumentRequest(BaseModel):
    source_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    content: str = Field(..., min_length=1, max_length=100_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content cannot be blank")
        return value


class IngestDocumentResponse(BaseModel):
    source_id: str
    chunks_written: int


class RetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query cannot be blank")
        return value


class RetrievalResult(BaseModel):
    source_id: str
    chunk_index: int
    content: str
    metadata: dict[str, Any]
    similarity: float


class RetrievalResponse(BaseModel):
    results: list[RetrievalResult]


class ValidateVendorInput(BaseModel):
    vendor_id: int = Field(..., gt=0, lt=1_000_000)


class CheckBudgetInput(BaseModel):
    department_id: int = Field(..., gt=0, lt=1_000)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if value > Decimal("999999.99"):
            raise ValueError("amount too large")
        return value


class DetectDuplicatesInput(BaseModel):
    vendor_id: int = Field(..., gt=0, lt=1_000_000)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    date: str

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")
        return value


class ProcessPaymentInput(BaseModel):
    invoice_id: str = Field(..., min_length=1, max_length=50)
    vendor_id: int = Field(..., gt=0, lt=1_000_000)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)


class ValidateVendorOutput(BaseModel):
    is_approved: bool
    risk_level: str
    reason: str


class CheckBudgetOutput(BaseModel):
    has_budget: bool
    available: Decimal
    required: Decimal
    reason: str


class DetectDuplicatesOutput(BaseModel):
    is_duplicate: bool
    matching_invoices: list[str]
    reason: str


class ProcessPaymentOutput(BaseModel):
    success: bool
    transaction_id: Optional[str]
    reason: str
    idempotency_key: Optional[str] = None
    was_replayed: bool = False


class ToolCall(BaseModel):
    name: str = Field(..., pattern=r"^[a-z_]+$")
    input: dict[str, Any]


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    execution_id: Optional[int] = None

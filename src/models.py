"""
Pydantic models for type-safe validation.

Every LLM tool call and HTTP payload is validated before it reaches SQL.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class RequestDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"
    ERROR = "error"


class AgentDecisionOutput(BaseModel):
    decision: Literal["approved", "rejected", "needs_review"]
    reason: str = Field(..., min_length=1, max_length=500)


class ProcessRequest(BaseModel):
    request_id: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    vendor_id: int = Field(..., gt=0, lt=1_000_000)
    vendor_name: str = Field(..., min_length=1, max_length=255)
    unit_id: int = Field(..., gt=0, lt=1_000)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    date: str
    message: str = Field(default="", max_length=4_000)
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


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=4_000)


class ChatImage(BaseModel):
    media_type: Literal["image/jpeg", "image/png", "image/webp", "image/gif"]
    data: str = Field(..., min_length=8, max_length=4_000_000)


class IntakeDraft(BaseModel):
    message: str = ""
    unit_id: Optional[int] = None
    unit_label: str = ""
    vendor_id: Optional[int] = None
    vendor_name: str = ""
    amount: Optional[Decimal] = None
    date: str = ""


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=40)
    draft: IntakeDraft = Field(default_factory=IntakeDraft)
    image: Optional[ChatImage] = None

    @field_validator("messages")
    @classmethod
    def last_message_is_user(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if value[-1].role != "user":
            raise ValueError("last message must be from the user")
        return value

    @model_validator(mode="after")
    def require_text_or_image(self) -> "ChatRequest":
        if not self.messages[-1].content.strip() and self.image is None:
            raise ValueError("Send a message or a photo")
        return self


class ProcessRequestResponse(BaseModel):
    execution_id: int
    request_id: str
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


class LookupUnitInput(BaseModel):
    unit_id: int = Field(..., gt=0, lt=1_000)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if value > Decimal("999999.99"):
            raise ValueError("amount too large")
        return value


class DetectOpenWorkOrdersInput(BaseModel):
    unit_id: int = Field(..., gt=0, lt=1_000)
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


class CreateWorkOrderInput(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=50)
    vendor_id: int = Field(..., gt=0, lt=1_000_000)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)


class ValidateVendorOutput(BaseModel):
    is_approved: bool
    risk_level: str
    reason: str


class LookupUnitOutput(BaseModel):
    found: bool
    has_budget: bool
    available: Decimal
    required: Decimal
    reason: str


class DetectOpenWorkOrdersOutput(BaseModel):
    is_duplicate: bool
    matching_requests: list[str]
    reason: str


class CreateWorkOrderOutput(BaseModel):
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

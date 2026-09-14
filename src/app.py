from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent import process_invoice_workflow
from src.config import settings
from src.database import AsyncSessionLocal, Database, get_db_session, init_db
from src.models import (
    BackgroundJobResponse,
    HumanReviewRequest,
    HumanReviewResponse,
    IngestDocumentRequest,
    IngestDocumentResponse,
    ProcessInvoiceRequest,
    ProcessInvoiceResponse,
    RetrievalRequest,
    RetrievalResponse,
)
from src.retrieval import ingest_document, search_documents
from src.seed import seed_if_empty
from src.tools import execute_tool


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.is_sqlite:
        await init_db()
    async with AsyncSessionLocal() as session:
        await seed_if_empty(session, settings)
    yield


app = FastAPI(
    title="WorkCore Invoice Agent",
    description="ReACT invoice agent with parallel tools, idempotency, human review, and an audit trail.",
    version="1.1.0",
    lifespan=lifespan,
)

bearer_scheme = HTTPBearer(auto_error=False)
DEMO_PAGE = Path(__file__).resolve().parent.parent / "static" / "index.html"


async def current_tenant(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    api_key = credentials.credentials.strip()
    db = Database(session)
    tenant = await db.get_tenant_by_api_key(api_key)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    await db.set_tenant_context(tenant["id"])
    return tenant


def _to_response(
    execution_id: int,
    invoice_id: str,
    decision: str,
    reason: str,
    iterations: int,
    tokens_used: int,
    duration_ms: int,
    cached: bool = False,
) -> ProcessInvoiceResponse:
    return ProcessInvoiceResponse(
        execution_id=execution_id,
        invoice_id=invoice_id,
        decision=decision,
        reason=reason,
        iterations=iterations,
        tokens_used=tokens_used,
        duration_ms=duration_ms,
        cached=cached,
    )


def _execution_matches_invoice(execution: dict, invoice: ProcessInvoiceRequest) -> bool:
    return (
        execution["invoice_id"] == invoice.invoice_id
        and execution["vendor_id"] == invoice.vendor_id
        and execution["department_id"] == invoice.department_id
        and Decimal(str(execution["amount"])) == invoice.amount
        and execution["invoice_date"] in (None, invoice.date)
    )


def _cached_response(execution: dict) -> ProcessInvoiceResponse:
    return _to_response(
        execution["id"],
        execution["invoice_id"],
        execution["decision"],
        execution["reason"] or "",
        execution["iterations"] or 0,
        execution["tokens_used"] or 0,
        execution["duration_ms"] or 0,
        cached=True,
    )


async def run_invoice_execution(
    db: Database,
    tenant: dict,
    invoice: ProcessInvoiceRequest,
    on_event=None,
) -> ProcessInvoiceResponse:
    started = time.perf_counter()

    if invoice.idempotency_key:
        cached = await db.get_execution_by_idempotency_key(tenant["id"], invoice.idempotency_key)
        if cached is not None:
            if not _execution_matches_invoice(cached, invoice):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key was already used for a different invoice payload",
                )
            if cached["state"] != "running":
                return _cached_response(cached)

    department = await db.get_department(tenant["id"], invoice.department_id)
    department_name = department["name"] if department else f"Department {invoice.department_id}"

    job_id = await db.create_job(tenant["id"], invoice.invoice_id)
    execution_id, is_owner = await db.acquire_execution(
        tenant_id=tenant["id"],
        job_id=job_id,
        idempotency_key=invoice.idempotency_key,
        invoice_id=invoice.invoice_id,
        vendor_id=invoice.vendor_id,
        department_id=invoice.department_id,
        amount=invoice.amount,
        invoice_date=invoice.date,
    )
    existing = await db.get_execution(tenant["id"], execution_id)
    if existing is None:
        raise HTTPException(status_code=500, detail="Execution ownership could not be resolved")
    if not _execution_matches_invoice(existing, invoice):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invoice ID was already used for a different payload",
        )

    if not is_owner:
        completed = await db.wait_for_execution(
            tenant["id"],
            execution_id,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS + 5,
        )
        if completed is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The original request is still processing; retry with the same idempotency key",
            )
        return _cached_response(completed)

    claimed_job_id = await db.claim_job(tenant["id"], invoice.invoice_id, worker_id=1)
    if claimed_job_id != job_id:
        await db.update_execution_complete(
            execution_id,
            decision="error",
            reason="Job ownership could not be acquired",
            iterations=0,
            tokens_used=0,
            duration_ms=0,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invoice is already owned by another worker",
        )

    decision, reason, iterations, tokens_used = await process_invoice_workflow(
        db=db,
        tenant_id=tenant["id"],
        execution_id=execution_id,
        invoice_id=invoice.invoice_id,
        vendor_id=invoice.vendor_id,
        department_id=invoice.department_id,
        amount=invoice.amount,
        date=invoice.date,
        department_name=department_name,
        vendor_name=invoice.vendor_name,
        on_event=on_event,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    await db.update_execution_complete(
        execution_id,
        decision=str(decision),
        reason=reason,
        iterations=iterations,
        tokens_used=tokens_used,
        duration_ms=duration_ms,
    )
    if decision == "error":
        await db.fail_job(job_id, reason)
    elif decision == "needs_review":
        await db.set_job_awaiting_review(job_id)
    else:
        await db.complete_job(job_id)

    return _to_response(
        execution_id,
        invoice.invoice_id,
        str(decision),
        reason,
        iterations,
        tokens_used,
        duration_ms,
    )


@app.get("/", include_in_schema=False)
async def demo_page() -> FileResponse:
    return FileResponse(DEMO_PAGE)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "llm": settings.llm_provider,
        "llm_model": settings.active_llm_model,
    }


@app.post(
    "/documents",
    response_model=IngestDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    document: IngestDocumentRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> IngestDocumentResponse:
    chunks_written = await ingest_document(
        db=Database(session),
        tenant_id=tenant["id"],
        source_id=document.source_id,
        content=document.content,
        metadata=document.metadata,
    )
    return IngestDocumentResponse(
        source_id=document.source_id,
        chunks_written=chunks_written,
    )


@app.post("/retrieve", response_model=RetrievalResponse)
async def retrieve_documents(
    request: RetrievalRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> RetrievalResponse:
    results = await search_documents(
        db=Database(session),
        tenant_id=tenant["id"],
        query=request.query,
        limit=request.limit,
    )
    return RetrievalResponse(results=results)


@app.post("/process-invoice", response_model=ProcessInvoiceResponse)
async def process_invoice(
    invoice: ProcessInvoiceRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> ProcessInvoiceResponse:
    return await run_invoice_execution(Database(session), tenant, invoice)


@app.post(
    "/invoice-jobs",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_invoice(
    invoice: ProcessInvoiceRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> BackgroundJobResponse:
    """Persist work and return immediately; `python -m src.worker` executes it."""
    db = Database(session)
    if invoice.idempotency_key:
        existing = await db.get_execution_by_idempotency_key(
            tenant["id"],
            invoice.idempotency_key,
        )
        if existing is not None and not _execution_matches_invoice(existing, invoice):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used for a different invoice payload",
            )

    job_id = await db.create_job(tenant["id"], invoice.invoice_id)
    execution_id, is_owner = await db.acquire_execution(
        tenant_id=tenant["id"],
        job_id=job_id,
        idempotency_key=invoice.idempotency_key,
        invoice_id=invoice.invoice_id,
        vendor_id=invoice.vendor_id,
        department_id=invoice.department_id,
        amount=invoice.amount,
        invoice_date=invoice.date,
    )
    execution = await db.get_execution(tenant["id"], execution_id)
    if execution is None or not _execution_matches_invoice(execution, invoice):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invoice ID was already used for a different payload",
        )
    job = await db.get_job(job_id)
    return BackgroundJobResponse(
        job_id=job_id,
        execution_id=execution_id,
        status=job["status"],
        decision=execution["decision"],
        cached=not is_owner,
    )


@app.get("/invoice-jobs/{job_id}", response_model=BackgroundJobResponse)
async def get_invoice_job(
    job_id: int,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> BackgroundJobResponse:
    db = Database(session)
    job = await db.get_job(job_id)
    if job is None or job["tenant_id"] != tenant["id"]:
        raise HTTPException(status_code=404, detail="Job not found")
    execution = await db.get_execution_by_job(job_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return BackgroundJobResponse(
        job_id=job_id,
        execution_id=execution["id"],
        status=job["status"],
        decision=execution["decision"],
        cached=True,
    )


@app.post("/process-invoice/stream")
async def process_invoice_stream(
    invoice: ProcessInvoiceRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Stream tool progress as SSE while the agent loop is still in flight.

    Events: started, context, tools, model_delta, decision, done, error.

    With Anthropic configured, model_delta contains real provider token
    chunks. The policy fallback has no artificial text stream.
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_event(event: dict) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            result = await run_invoice_execution(Database(session), tenant, invoice, on_event=on_event)
            await queue.put({"type": "done", **result.model_dump()})
        except Exception as error:
            await queue.put({"type": "error", "reason": str(error)})
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, default=str)}\n\n"
        finally:
            await task

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/executions/{execution_id}")
async def get_execution(
    execution_id: int,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    db = Database(session)
    execution = await db.get_execution(tenant["id"], execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    tools = await db.list_tool_invocations(execution_id)
    return {**execution, "tools": tools}


@app.post("/executions/{execution_id}/approve", response_model=HumanReviewResponse)
async def approve_execution(
    execution_id: int,
    body: HumanReviewRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> HumanReviewResponse:
    """A person must confirm anything at or above the $5000 threshold."""
    db = Database(session)
    execution = await db.get_execution(tenant["id"], execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if execution["decision"] != "needs_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution is '{execution['decision']}', not awaiting review",
        )
    if not await db.claim_human_review(tenant["id"], execution_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Execution is already being reviewed",
        )

    amount = Decimal(str(execution["amount"]))
    success, result = await execute_tool(
        "process_payment",
        {
            "invoice_id": execution["invoice_id"],
            "vendor_id": execution["vendor_id"],
            "amount": float(amount),
        },
        db,
        tenant["id"],
        execution_id=execution_id,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)

    reason = body.note or f"Human {body.reviewer} approved after review"
    await db.log_tool_invocation(
        tenant["id"],
        execution_id,
        "human_approve",
        {"reviewer": body.reviewer, "note": body.note},
        result if isinstance(result, dict) else {"result": result},
        None,
        0,
        0,
    )
    await db.update_execution_complete(
        execution_id,
        decision="approved",
        reason=reason,
        iterations=execution["iterations"] or 0,
        tokens_used=execution["tokens_used"] or 0,
        duration_ms=execution["duration_ms"] or 0,
    )
    await db.complete_job(execution["job_id"])
    return HumanReviewResponse(
        execution_id=execution_id,
        decision="approved",
        reason=reason,
        reviewer=body.reviewer,
    )


@app.post("/executions/{execution_id}/reject", response_model=HumanReviewResponse)
async def reject_execution(
    execution_id: int,
    body: HumanReviewRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> HumanReviewResponse:
    db = Database(session)
    execution = await db.get_execution(tenant["id"], execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if execution["decision"] != "needs_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution is '{execution['decision']}', not awaiting review",
        )
    if not await db.claim_human_review(tenant["id"], execution_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Execution is already being reviewed",
        )

    reason = body.note or f"Human {body.reviewer} rejected after review"
    await db.log_tool_invocation(
        tenant["id"],
        execution_id,
        "human_reject",
        {"reviewer": body.reviewer, "note": body.note},
        {"decision": "rejected"},
        None,
        0,
        0,
    )
    await db.update_execution_complete(
        execution_id,
        decision="rejected",
        reason=reason,
        iterations=execution["iterations"] or 0,
        tokens_used=execution["tokens_used"] or 0,
        duration_ms=execution["duration_ms"] or 0,
    )
    await db.complete_job(execution["job_id"])
    return HumanReviewResponse(
        execution_id=execution_id,
        decision="rejected",
        reason=reason,
        reviewer=body.reviewer,
    )

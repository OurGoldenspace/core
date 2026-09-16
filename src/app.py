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

from src.agent import process_request_workflow
from src.config import settings
from src.database import AsyncSessionLocal, Database, get_db_session, init_db
from src.intake import interpret_turn, missing_slots
from src.models import (
    BackgroundJobResponse,
    ChatRequest,
    HumanReviewRequest,
    HumanReviewResponse,
    IngestDocumentRequest,
    IngestDocumentResponse,
    ProcessRequest,
    ProcessRequestResponse,
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
    title="WorkCore Maintenance Agent",
    description="ReACT maintenance agent with parallel tools, idempotency, human review, and an audit trail.",
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
    request_id: str,
    decision: str,
    reason: str,
    iterations: int,
    tokens_used: int,
    duration_ms: int,
    cached: bool = False,
) -> ProcessRequestResponse:
    return ProcessRequestResponse(
        execution_id=execution_id,
        request_id=request_id,
        decision=decision,
        reason=reason,
        iterations=iterations,
        tokens_used=tokens_used,
        duration_ms=duration_ms,
        cached=cached,
    )


def _execution_matches_request(execution: dict, request: ProcessRequest) -> bool:
    return (
        execution["request_id"] == request.request_id
        and execution["vendor_id"] == request.vendor_id
        and execution["unit_id"] == request.unit_id
        and Decimal(str(execution["amount"])) == request.amount
        and execution["reported_date"] in (None, request.date)
    )


def _cached_response(execution: dict) -> ProcessRequestResponse:
    return _to_response(
        execution["id"],
        execution["request_id"],
        execution["decision"],
        execution["reason"] or "",
        execution["iterations"] or 0,
        execution["tokens_used"] or 0,
        execution["duration_ms"] or 0,
        cached=True,
    )


async def run_request_execution(
    db: Database,
    tenant: dict,
    request: ProcessRequest,
    on_event=None,
) -> ProcessRequestResponse:
    started = time.perf_counter()

    if request.idempotency_key:
        cached = await db.get_execution_by_idempotency_key(tenant["id"], request.idempotency_key)
        if cached is not None:
            if not _execution_matches_request(cached, request):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key was already used for a different request payload",
                )
            if cached["state"] != "running":
                return _cached_response(cached)

    unit = await db.get_unit(tenant["id"], request.unit_id)
    unit_name = unit["name"] if unit else f"Unit {request.unit_id}"

    job_id = await db.create_job(tenant["id"], request.request_id)
    execution_id, is_owner = await db.acquire_execution(
        tenant_id=tenant["id"],
        job_id=job_id,
        idempotency_key=request.idempotency_key,
        request_id=request.request_id,
        vendor_id=request.vendor_id,
        unit_id=request.unit_id,
        amount=request.amount,
        reported_date=request.date,
    )
    existing = await db.get_execution(tenant["id"], execution_id)
    if existing is None:
        raise HTTPException(status_code=500, detail="Execution ownership could not be resolved")
    if not _execution_matches_request(existing, request):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request ID was already used for a different payload",
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

    claimed_job_id = await db.claim_job(tenant["id"], request.request_id, worker_id=1)
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
            detail="Request is already owned by another worker",
        )

    decision, reason, iterations, tokens_used = await process_request_workflow(
        db=db,
        tenant_id=tenant["id"],
        execution_id=execution_id,
        request_id=request.request_id,
        vendor_id=request.vendor_id,
        unit_id=request.unit_id,
        amount=request.amount,
        date=request.date,
        unit_name=unit_name,
        vendor_name=request.vendor_name,
        message=request.message,
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
        request.request_id,
        str(decision),
        reason,
        iterations,
        tokens_used,
        duration_ms,
    )


def _sse_line(event: dict) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, default=str)}\n\n"


def _execution_stream(session: AsyncSession, tenant: dict, request: ProcessRequest):
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_event(event: dict) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            result = await run_request_execution(Database(session), tenant, request, on_event=on_event)
            await session.commit()
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
                yield _sse_line(event)
        finally:
            await task

    return StreamingResponse(events(), media_type="text/event-stream")


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


@app.post("/process-request", response_model=ProcessRequestResponse)
async def process_request(
    request: ProcessRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> ProcessRequestResponse:
    return await run_request_execution(Database(session), tenant, request)


@app.post(
    "/request-jobs",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_request(
    request: ProcessRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
) -> BackgroundJobResponse:
    """Persist work and return immediately; `python -m src.worker` executes it."""
    db = Database(session)
    if request.idempotency_key:
        existing = await db.get_execution_by_idempotency_key(
            tenant["id"],
            request.idempotency_key,
        )
        if existing is not None and not _execution_matches_request(existing, request):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used for a different request payload",
            )

    job_id = await db.create_job(tenant["id"], request.request_id)
    execution_id, is_owner = await db.acquire_execution(
        tenant_id=tenant["id"],
        job_id=job_id,
        idempotency_key=request.idempotency_key,
        request_id=request.request_id,
        vendor_id=request.vendor_id,
        unit_id=request.unit_id,
        amount=request.amount,
        reported_date=request.date,
    )
    execution = await db.get_execution(tenant["id"], execution_id)
    if execution is None or not _execution_matches_request(execution, request):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request ID was already used for a different payload",
        )
    job = await db.get_job(job_id)
    return BackgroundJobResponse(
        job_id=job_id,
        execution_id=execution_id,
        status=job["status"],
        decision=execution["decision"],
        cached=not is_owner,
    )


@app.get("/request-jobs/{job_id}", response_model=BackgroundJobResponse)
async def get_request_job(
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


@app.post("/process-request/stream")
async def process_request_stream(
    request: ProcessRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Stream tool progress as SSE while the agent loop is still in flight.

    Events: started, context, tools, model_delta, decision, done, error.

    With Anthropic configured, model_delta contains real provider token
    chunks. The policy fallback has no artificial text stream.
    """
    return _execution_stream(session, tenant, request)


@app.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    tenant: dict = Depends(current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Collect missing maintenance details in chat, then run the same agent loop."""
    db = Database(session)
    units = await db.list_units(tenant["id"])
    vendors = await db.list_vendors(tenant["id"])
    image = None if body.image is None else body.image.model_dump()
    draft, reply, ready = await interpret_turn(
        draft=body.draft.model_dump(),
        user_text=body.messages[-1].content,
        units=units,
        vendors=vendors,
        history=[item.model_dump() for item in body.messages[:-1]],
        image=image,
    )
    if not ready:
        async def ask():
            yield _sse_line(
                {
                    "type": "ask",
                    "text": reply,
                    "draft": draft,
                    "missing": missing_slots(draft),
                }
            )

        return StreamingResponse(ask(), media_type="text/event-stream")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    request = ProcessRequest(
        request_id=f"WO-CHAT-{stamp}",
        vendor_id=int(draft["vendor_id"]),
        vendor_name=str(draft["vendor_name"]),
        unit_id=int(draft["unit_id"]),
        amount=Decimal(str(draft["amount"])),
        date=str(draft["date"]),
        message=str(draft["message"]),
        idempotency_key=f"chat-{stamp}",
    )

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_event(event: dict) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            result = await run_request_execution(Database(session), tenant, request, on_event=on_event)
            await session.commit()
            await queue.put({"type": "done", **result.model_dump()})
        except Exception as error:
            await queue.put({"type": "error", "reason": str(error)})
        finally:
            await queue.put(None)

    async def events():
        yield _sse_line({"type": "working", "text": reply, "draft": draft})
        task = asyncio.create_task(run())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse_line(event)
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
    """A person must confirm anything at or above the $5000 owner-approval threshold."""
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
        "create_work_order",
        {
            "request_id": execution["request_id"],
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

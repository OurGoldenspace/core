"""
Async database layer.

Postgres interview path uses FOR UPDATE SKIP LOCKED.
Local SQLite uses an atomic UPDATE ... WHERE status = 'pending'.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from src.config import get_settings, settings

logger = logging.getLogger(__name__)


@event.listens_for(Session, "after_begin")
def _restore_postgres_tenant_context(session, _transaction, connection) -> None:
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None or connection.dialect.name != "postgresql":
        return
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )

SQLITE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS tenants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        api_key TEXT NOT NULL UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vendors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        vendor_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        is_approved INTEGER NOT NULL DEFAULT 1,
        risk_level TEXT,
        credit_limit NUMERIC(12, 2),
        ytd_spent NUMERIC(12, 2) DEFAULT 0,
        country TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (tenant_id, vendor_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        dept_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        budget_annual NUMERIC(12, 2),
        budget_spent NUMERIC(12, 2) DEFAULT 0,
        budget_available NUMERIC(12, 2),
        approval_threshold NUMERIC(12, 2),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (tenant_id, dept_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        invoice_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        claimed_by INTEGER,
        claimed_at TIMESTAMP,
        retry_count INTEGER DEFAULT 0,
        last_error TEXT,
        available_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (tenant_id, invoice_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        job_id INTEGER REFERENCES jobs(id),
        idempotency_key TEXT,
        invoice_id TEXT NOT NULL,
        vendor_id INTEGER,
        department_id INTEGER,
        amount NUMERIC(12, 2),
        invoice_date DATE,
        state TEXT NOT NULL DEFAULT 'running',
        decision TEXT,
        reason TEXT,
        iterations INTEGER,
        tokens_used INTEGER,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        duration_ms INTEGER,
        UNIQUE (tenant_id, idempotency_key),
        UNIQUE (job_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        execution_id INTEGER NOT NULL REFERENCES executions(id),
        invoice_id TEXT NOT NULL,
        vendor_id INTEGER NOT NULL,
        amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
        idempotency_key TEXT NOT NULL,
        transaction_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'succeeded',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (tenant_id, invoice_id),
        UNIQUE (tenant_id, idempotency_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_invocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        execution_id INTEGER NOT NULL REFERENCES executions(id),
        tool_name TEXT NOT NULL,
        tool_input TEXT,
        tool_result TEXT,
        validation_error TEXT,
        iteration_number INTEGER,
        duration_ms INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        execution_id INTEGER NOT NULL REFERENCES executions(id),
        model TEXT,
        max_tokens INTEGER,
        temperature NUMERIC(3, 2),
        completion_tokens INTEGER,
        prompt_tokens INTEGER,
        total_tokens INTEGER,
        stop_reason TEXT,
        duration_ms INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        source_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        embedding TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (tenant_id, source_id, chunk_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs (tenant_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_tool_invocations_execution ON tool_invocations (execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_payments_execution ON payments (execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_document_chunks_source ON document_chunks (tenant_id, source_id)",
]


def _connect_args(url: str) -> dict[str, Any]:
    if url.startswith("sqlite"):
        return {"timeout": 30}
    return {}


def _attach_sqlite_pragma(engine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragma(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_engine(url: str | None = None):
    app_settings = get_settings()
    url = url or app_settings.DATABASE_URL
    engine = create_async_engine(
        url,
        echo=app_settings.DEBUG,
        poolclass=NullPool if url.startswith("sqlite") or app_settings.DEBUG else None,
        connect_args=_connect_args(url),
    )
    if url.startswith("sqlite"):
        _attach_sqlite_pragma(engine)
    return engine


async_engine = create_engine()
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


def get_session_factory():
    return AsyncSessionLocal


async def reset_engine() -> None:
    """Rebuild the engine after tests change DATABASE_URL."""
    global async_engine, AsyncSessionLocal
    await async_engine.dispose()
    async_engine = create_engine()
    AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session():
    async with AsyncSessionLocal() as session:
        yield session


def _split_sql(sql: str) -> list[str]:
    statements = []
    for chunk in sql.split(";"):
        cleaned = "\n".join(
            line for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ).strip()
        if cleaned:
            statements.append(cleaned)
    return statements


async def _ensure_sqlite_job_columns(conn) -> None:
    result = await conn.execute(text("PRAGMA table_info(jobs)"))
    columns = {row[1] for row in result.fetchall()}
    alterations = {
        "retry_count": "ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0",
        "last_error": "ALTER TABLE jobs ADD COLUMN last_error TEXT",
        "available_at": "ALTER TABLE jobs ADD COLUMN available_at TIMESTAMP",
        "completed_at": "ALTER TABLE jobs ADD COLUMN completed_at TIMESTAMP",
    }
    for name, statement in alterations.items():
        if name not in columns:
            await conn.execute(text(statement))


async def _ensure_sqlite_execution_columns(conn) -> None:
    result = await conn.execute(text("PRAGMA table_info(executions)"))
    columns = {row[1] for row in result.fetchall()}
    alterations = {
        "invoice_date": "ALTER TABLE executions ADD COLUMN invoice_date DATE",
        "state": "ALTER TABLE executions ADD COLUMN state TEXT NOT NULL DEFAULT 'running'",
    }
    for name, statement in alterations.items():
        if name not in columns:
            await conn.execute(text(statement))
    await conn.execute(
        text(
            """
            UPDATE executions
            SET state = CASE
                WHEN decision = 'needs_review' THEN 'awaiting_review'
                WHEN decision = 'error' THEN 'failed'
                ELSE 'completed'
            END
            WHERE decision IS NOT NULL AND state = 'running'
            """
        )
    )
    # Legacy demo databases allowed several executions to reference one job.
    # Keep every audit row, but detach older duplicates before enforcing the
    # new one-execution-per-job ownership invariant.
    await conn.execute(
        text(
            """
            UPDATE executions
            SET job_id = NULL
            WHERE job_id IS NOT NULL
              AND id NOT IN (
                  SELECT MAX(id)
                  FROM executions
                  WHERE job_id IS NOT NULL
                  GROUP BY job_id
              )
            """
        )
    )


async def apply_schema(conn, url: str | None = None) -> None:
    app_settings = get_settings()
    using_sqlite = (url or app_settings.DATABASE_URL).startswith("sqlite")
    if using_sqlite:
        for statement in SQLITE_STATEMENTS:
            await conn.execute(text(statement))
        await _ensure_sqlite_job_columns(conn)
        await _ensure_sqlite_execution_columns(conn)
        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_job_unique
                ON executions (job_id)
                WHERE job_id IS NOT NULL
                """
            )
        )
        return
    import os

    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    schema = open(schema_path, encoding="utf-8").read()
    for statement in _split_sql(schema):
        await conn.execute(text(statement))


async def init_db() -> None:
    logger.info("Initializing database...")
    async with async_engine.begin() as conn:
        await apply_schema(conn)
    logger.info("Database initialized")


class Database:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def dialect(self) -> str:
        bind = self.session.get_bind()
        if bind is not None:
            return bind.dialect.name
        return "sqlite" if settings.is_sqlite else "postgresql"

    async def set_tenant_context(self, tenant_id: int) -> None:
        """
        Scope the active and future transactions for PostgreSQL RLS.

        SET LOCAL is restored by the Session after_begin hook after each commit,
        preventing tenant context from leaking through pooled connections.
        """
        self.session.info["tenant_id"] = tenant_id
        if self.dialect != "postgresql":
            return
        await self.session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )

    async def get_tenant_by_api_key(self, api_key: str) -> Optional[dict]:
        result = await self.session.execute(
            text("SELECT id, name FROM tenants WHERE api_key = :key"),
            {"key": api_key},
        )
        row = result.fetchone()
        if row:
            return {"id": row[0], "name": row[1]}
        return None

    async def get_vendor(self, tenant_id: int, vendor_id: int) -> Optional[dict]:
        result = await self.session.execute(
            text(
                """
                SELECT id, name, is_approved, risk_level, credit_limit, ytd_spent
                FROM vendors
                WHERE tenant_id = :tenant_id AND vendor_id = :vendor_id
                """
            ),
            {"tenant_id": tenant_id, "vendor_id": vendor_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "is_approved": bool(row[2]),
            "risk_level": row[3],
            "credit_limit": row[4],
            "ytd_spent": row[5],
        }

    async def get_department(self, tenant_id: int, dept_id: int) -> Optional[dict]:
        result = await self.session.execute(
            text(
                """
                SELECT id, name, budget_annual, budget_spent,
                       COALESCE(budget_available, budget_annual - budget_spent),
                       approval_threshold
                FROM departments
                WHERE tenant_id = :tenant_id AND dept_id = :dept_id
                """
            ),
            {"tenant_id": tenant_id, "dept_id": dept_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "budget_annual": row[2],
            "budget_spent": row[3],
            "budget_available": row[4],
            "approval_threshold": row[5],
        }

    async def claim_job(self, tenant_id: int, invoice_id: str, worker_id: int) -> Optional[int]:
        if self.dialect == "postgresql":
            result = await self.session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'claimed', claimed_by = :worker_id, claimed_at = CURRENT_TIMESTAMP
                    WHERE id = (
                        SELECT id FROM jobs
                        WHERE tenant_id = :tenant_id
                          AND status = 'pending'
                          AND COALESCE(available_at, CURRENT_TIMESTAMP) <= CURRENT_TIMESTAMP
                          AND invoice_id = :invoice_id
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id
                    """
                ),
                {"tenant_id": tenant_id, "invoice_id": invoice_id, "worker_id": worker_id},
            )
        else:
            result = await self.session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'claimed', claimed_by = :worker_id, claimed_at = CURRENT_TIMESTAMP
                    WHERE id = (
                        SELECT id FROM jobs
                        WHERE tenant_id = :tenant_id
                          AND status = 'pending'
                          AND COALESCE(available_at, CURRENT_TIMESTAMP) <= CURRENT_TIMESTAMP
                          AND invoice_id = :invoice_id
                        ORDER BY created_at
                        LIMIT 1
                    )
                    AND status = 'pending'
                    """
                ),
                {"tenant_id": tenant_id, "invoice_id": invoice_id, "worker_id": worker_id},
            )
            if result.rowcount == 0:
                await self.session.commit()
                return None
            claimed = await self.session.execute(
                text(
                    """
                    SELECT id FROM jobs
                    WHERE tenant_id = :tenant_id AND invoice_id = :invoice_id AND claimed_by = :worker_id
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "invoice_id": invoice_id, "worker_id": worker_id},
            )
            await self.session.commit()
            row = claimed.fetchone()
            return row[0] if row else None

        await self.session.commit()
        row = result.fetchone()
        return row[0] if row else None

    async def claim_next_job(self, worker_id: int) -> Optional[int]:
        """Claim any pending job. Used by the exactly-once worker test."""
        if self.dialect == "postgresql":
            result = await self.session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'claimed', claimed_by = :worker_id, claimed_at = CURRENT_TIMESTAMP
                    WHERE id = (
                        SELECT id FROM jobs
                        WHERE status = 'pending'
                          AND COALESCE(available_at, CURRENT_TIMESTAMP) <= CURRENT_TIMESTAMP
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id
                    """
                ),
                {"worker_id": worker_id},
            )
            await self.session.commit()
            row = result.fetchone()
            return row[0] if row else None

        result = await self.session.execute(
            text(
                """
                UPDATE jobs
                SET status = 'claimed', claimed_by = :worker_id, claimed_at = CURRENT_TIMESTAMP
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE status = 'pending'
                      AND COALESCE(available_at, CURRENT_TIMESTAMP) <= CURRENT_TIMESTAMP
                    ORDER BY id
                    LIMIT 1
                )
                AND status = 'pending'
                """
            ),
            {"worker_id": worker_id},
        )
        if result.rowcount == 0:
            await self.session.commit()
            return None
        claimed = await self.session.execute(
            text(
                "SELECT id FROM jobs WHERE claimed_by = :worker_id AND status = 'claimed' ORDER BY id DESC LIMIT 1"
            ),
            {"worker_id": worker_id},
        )
        await self.session.commit()
        row = claimed.fetchone()
        return row[0] if row else None

    async def create_job(self, tenant_id: int, invoice_id: str) -> int:
        result = await self.session.execute(
            text(
                """
                INSERT INTO jobs (tenant_id, invoice_id, status, available_at)
                VALUES (:tenant_id, :invoice_id, 'pending', CURRENT_TIMESTAMP)
                ON CONFLICT (tenant_id, invoice_id) DO NOTHING
                RETURNING id
                """
            ),
            {"tenant_id": tenant_id, "invoice_id": invoice_id},
        )
        await self.session.commit()
        row = result.fetchone()
        if row is not None:
            return row[0]

        existing = await self.session.execute(
            text(
                "SELECT id FROM jobs WHERE tenant_id = :tenant_id AND invoice_id = :invoice_id"
            ),
            {"tenant_id": tenant_id, "invoice_id": invoice_id},
        )
        return existing.scalar_one()

    async def acquire_execution(
        self,
        tenant_id: int,
        job_id: int,
        idempotency_key: Optional[str],
        invoice_id: str,
        vendor_id: int,
        department_id: int,
        amount: Decimal,
        invoice_date: str | None = None,
    ) -> tuple[int, bool]:
        """
        Create the single execution for a job.

        The database arbitrates ownership. Exactly one caller receives
        is_owner=True; every concurrent retry observes the same row.
        """
        result = await self.session.execute(
            text(
                """
                INSERT INTO executions (
                    tenant_id, job_id, idempotency_key, invoice_id,
                    vendor_id, department_id, amount, invoice_date, state
                )
                VALUES (
                    :tenant_id, :job_id, :idempotency_key, :invoice_id,
                    :vendor_id, :department_id, :amount, :invoice_date, 'running'
                )
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "job_id": job_id,
                "idempotency_key": idempotency_key,
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "department_id": department_id,
                "amount": float(amount),
                "invoice_date": invoice_date,
            },
        )
        await self.session.commit()
        row = result.fetchone()
        if row is not None:
            return row[0], True

        existing = await self.session.execute(
            text(
                """
                SELECT id FROM executions
                WHERE tenant_id = :tenant_id
                  AND (
                    job_id = :job_id
                    OR (:idempotency_key IS NOT NULL AND idempotency_key = :idempotency_key)
                  )
                ORDER BY CASE WHEN job_id = :job_id THEN 0 ELSE 1 END
                LIMIT 1
                """
            ),
            {
                "tenant_id": tenant_id,
                "job_id": job_id,
                "idempotency_key": idempotency_key,
            },
        )
        return existing.scalar_one(), False

    async def create_execution(
        self,
        tenant_id: int,
        job_id: int,
        idempotency_key: Optional[str],
        invoice_id: str,
        vendor_id: int,
        department_id: int,
        amount: Decimal,
        invoice_date: str | None = None,
    ) -> int:
        execution_id, _ = await self.acquire_execution(
            tenant_id=tenant_id,
            job_id=job_id,
            idempotency_key=idempotency_key,
            invoice_id=invoice_id,
            vendor_id=vendor_id,
            department_id=department_id,
            amount=amount,
            invoice_date=invoice_date,
        )
        return execution_id

    async def get_execution_by_idempotency_key(
        self,
        tenant_id: int,
        idempotency_key: str,
    ) -> Optional[dict]:
        result = await self.session.execute(
            text(
                """
                SELECT id, decision, reason, iterations, tokens_used, duration_ms,
                       invoice_id, vendor_id, department_id, amount, invoice_date, state
                FROM executions
                WHERE tenant_id = :tenant_id AND idempotency_key = :key
                """
            ),
            {"tenant_id": tenant_id, "key": idempotency_key},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "decision": row[1],
            "reason": row[2],
            "iterations": row[3],
            "tokens_used": row[4],
            "duration_ms": row[5],
            "invoice_id": row[6],
            "vendor_id": row[7],
            "department_id": row[8],
            "amount": row[9],
            "invoice_date": str(row[10]) if row[10] is not None else None,
            "state": row[11],
        }

    async def get_execution(self, tenant_id: int, execution_id: int) -> Optional[dict]:
        result = await self.session.execute(
            text(
                """
                SELECT id, invoice_id, vendor_id, department_id, amount,
                       invoice_date, state, decision, reason, iterations,
                       tokens_used, duration_ms, job_id
                FROM executions
                WHERE tenant_id = :tenant_id AND id = :execution_id
                """
            ),
            {"tenant_id": tenant_id, "execution_id": execution_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "invoice_id": row[1],
            "vendor_id": row[2],
            "department_id": row[3],
            "amount": row[4],
            "invoice_date": str(row[5]) if row[5] is not None else None,
            "state": row[6],
            "decision": row[7],
            "reason": row[8],
            "iterations": row[9],
            "tokens_used": row[10],
            "duration_ms": row[11],
            "job_id": row[12],
        }

    async def wait_for_execution(
        self,
        tenant_id: int,
        execution_id: int,
        timeout_seconds: float,
    ) -> Optional[dict]:
        import asyncio
        import time

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            execution = await self.get_execution(tenant_id, execution_id)
            if execution is not None and execution["state"] != "running":
                return execution
            await self.session.rollback()
            await asyncio.sleep(0.025)
        return None

    async def list_tool_invocations(self, execution_id: int) -> list[dict]:
        result = await self.session.execute(
            text(
                """
                SELECT tool_name, duration_ms, tool_result
                FROM tool_invocations
                WHERE execution_id = :execution_id
                ORDER BY id
                """
            ),
            {"execution_id": execution_id},
        )
        return [
            {"tool_name": row[0], "duration_ms": row[1], "output": row[2]}
            for row in result.fetchall()
        ]

    async def update_execution_complete(
        self,
        execution_id: int,
        decision: str,
        reason: str,
        iterations: int,
        tokens_used: int,
        duration_ms: int,
    ) -> None:
        execution_state = {
            "needs_review": "awaiting_review",
            "error": "failed",
        }.get(decision, "completed")
        await self.session.execute(
            text(
                """
                UPDATE executions
                SET decision = :decision, reason = :reason, iterations = :iterations,
                    tokens_used = :tokens_used, duration_ms = :duration_ms,
                    state = :state,
                    completed_at = CASE
                        WHEN :state = 'awaiting_review' THEN NULL
                        ELSE CURRENT_TIMESTAMP
                    END
                WHERE id = :execution_id
                """
            ),
            {
                "execution_id": execution_id,
                "decision": decision,
                "reason": reason,
                "iterations": iterations,
                "tokens_used": tokens_used,
                "duration_ms": duration_ms,
                "state": execution_state,
            },
        )
        await self.session.commit()

    async def claim_human_review(self, tenant_id: int, execution_id: int) -> bool:
        result = await self.session.execute(
            text(
                """
                UPDATE executions
                SET state = 'reviewing'
                WHERE tenant_id = :tenant_id
                  AND id = :execution_id
                  AND state = 'awaiting_review'
                  AND decision = 'needs_review'
                RETURNING id
                """
            ),
            {"tenant_id": tenant_id, "execution_id": execution_id},
        )
        await self.session.commit()
        return result.fetchone() is not None

    async def set_job_awaiting_review(self, job_id: int) -> None:
        await self.session.execute(
            text(
                """
                UPDATE jobs
                SET status = 'awaiting_review', completed_at = NULL
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        )
        await self.session.commit()

    async def log_tool_invocation(
        self,
        tenant_id: int,
        execution_id: int,
        tool_name: str,
        tool_input: dict,
        tool_result: dict,
        validation_error: Optional[str],
        iteration_number: int,
        duration_ms: int,
    ) -> None:
        await self.session.execute(
            text(
                """
                INSERT INTO tool_invocations (
                    tenant_id, execution_id, tool_name, tool_input, tool_result,
                    validation_error, iteration_number, duration_ms
                )
                VALUES (
                    :tenant_id, :execution_id, :tool_name, :tool_input, :tool_result,
                    :validation_error, :iteration_number, :duration_ms
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "execution_id": execution_id,
                "tool_name": tool_name,
                "tool_input": json.dumps(tool_input, default=str),
                "tool_result": json.dumps(tool_result, default=str),
                "validation_error": validation_error,
                "iteration_number": iteration_number,
                "duration_ms": duration_ms,
            },
        )
        await self.session.flush()

    async def find_duplicate_invoices(
        self,
        tenant_id: int,
        vendor_id: int,
        amount: Decimal,
        date: str,
    ) -> list[str]:
        result = await self.session.execute(
            text(
                """
                SELECT invoice_id FROM executions
                WHERE tenant_id = :tenant_id
                  AND vendor_id = :vendor_id
                  AND amount = :amount
                  AND invoice_date = :date
                  AND decision IN ('approved', 'needs_review')
                LIMIT 5
                """
            ),
            {
                "tenant_id": tenant_id,
                "vendor_id": vendor_id,
                "amount": float(amount),
                "date": date,
            },
        )
        return [row[0] for row in result.fetchall()]

    async def reserve_payment(
        self,
        tenant_id: int,
        execution_id: int,
        invoice_id: str,
        vendor_id: int,
        amount: Decimal,
    ) -> tuple[dict, bool]:
        """
        Persist a payment intent before contacting a provider.

        The invoice-scoped key is also sent to the external provider. A retry
        can safely replay that key and receives the original transaction.
        """
        import uuid

        idempotency_key = f"payment:{tenant_id}:{invoice_id}"
        transaction_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"
        result = await self.session.execute(
            text(
                """
                INSERT INTO payments (
                    tenant_id, execution_id, invoice_id, vendor_id, amount,
                    idempotency_key, transaction_id, status
                )
                VALUES (
                    :tenant_id, :execution_id, :invoice_id, :vendor_id, :amount,
                    :idempotency_key, :transaction_id, 'pending'
                )
                ON CONFLICT DO NOTHING
                RETURNING id, transaction_id, idempotency_key, status
                """
            ),
            {
                "tenant_id": tenant_id,
                "execution_id": execution_id,
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "amount": float(amount),
                "idempotency_key": idempotency_key,
                "transaction_id": transaction_id,
            },
        )
        await self.session.commit()
        row = result.fetchone()
        is_owner = row is not None
        if row is None:
            existing = await self.session.execute(
                text(
                    """
                    SELECT id, transaction_id, idempotency_key, status
                    FROM payments
                    WHERE tenant_id = :tenant_id AND invoice_id = :invoice_id
                    """
                ),
                {"tenant_id": tenant_id, "invoice_id": invoice_id},
            )
            row = existing.one()
        return {
            "id": row[0],
            "transaction_id": row[1],
            "idempotency_key": row[2],
            "status": row[3],
        }, is_owner

    async def complete_payment(self, payment_id: int) -> None:
        await self.session.execute(
            text(
                """
                UPDATE payments
                SET status = 'succeeded'
                WHERE id = :payment_id AND status = 'pending'
                """
            ),
            {"payment_id": payment_id},
        )
        await self.session.commit()

    async def list_payments(self, tenant_id: int, invoice_id: str) -> list[dict]:
        result = await self.session.execute(
            text(
                """
                SELECT id, execution_id, invoice_id, transaction_id,
                       idempotency_key, status, amount
                FROM payments
                WHERE tenant_id = :tenant_id AND invoice_id = :invoice_id
                ORDER BY id
                """
            ),
            {"tenant_id": tenant_id, "invoice_id": invoice_id},
        )
        return [
            {
                "id": row[0],
                "execution_id": row[1],
                "invoice_id": row[2],
                "transaction_id": row[3],
                "idempotency_key": row[4],
                "status": row[5],
                "amount": row[6],
            }
            for row in result.fetchall()
        ]

    async def complete_job(self, job_id: int) -> None:
        await self.session.execute(
            text(
                """
                UPDATE jobs
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP, last_error = NULL
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        )
        await self.session.commit()

    async def fail_job(self, job_id: int, error: str) -> None:
        await self.session.execute(
            text(
                """
                UPDATE jobs
                SET status = 'failed', last_error = :error, completed_at = CURRENT_TIMESTAMP
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id, "error": error[:500]},
        )
        await self.session.commit()

    async def reclaim_stale_jobs(self, stale_seconds: int, max_retries: int) -> int:
        """
        Return claimed jobs whose worker died to pending.

        Postgres: claimed_at older than NOW() - interval.
        SQLite: claimed_at older than datetime('now', '-N seconds').
        """
        if self.dialect == "postgresql":
            dead = await self.session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'dead_letter',
                        last_error = COALESCE(last_error, 'Worker heartbeat expired'),
                        completed_at = CURRENT_TIMESTAMP
                    WHERE status = 'claimed'
                      AND claimed_at <= NOW() - (:stale_seconds * INTERVAL '1 second')
                      AND COALESCE(retry_count, 0) >= :max_retries
                    """
                ),
                {"stale_seconds": stale_seconds, "max_retries": max_retries},
            )
            result = await self.session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'pending',
                        claimed_by = NULL,
                        claimed_at = NULL,
                        retry_count = COALESCE(retry_count, 0) + 1,
                        available_at = CURRENT_TIMESTAMP
                    WHERE status = 'claimed'
                      AND claimed_at <= NOW() - (:stale_seconds * INTERVAL '1 second')
                      AND COALESCE(retry_count, 0) < :max_retries
                    """
                ),
                {"stale_seconds": stale_seconds, "max_retries": max_retries},
            )
        else:
            dead = await self.session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'dead_letter',
                        last_error = COALESCE(last_error, 'Worker heartbeat expired'),
                        completed_at = CURRENT_TIMESTAMP
                    WHERE status = 'claimed'
                      AND claimed_at <= datetime('now', :offset)
                      AND COALESCE(retry_count, 0) >= :max_retries
                    """
                ),
                {"offset": f"-{int(stale_seconds)} seconds", "max_retries": max_retries},
            )
            result = await self.session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'pending',
                        claimed_by = NULL,
                        claimed_at = NULL,
                        retry_count = COALESCE(retry_count, 0) + 1,
                        available_at = CURRENT_TIMESTAMP
                    WHERE status = 'claimed'
                      AND claimed_at <= datetime('now', :offset)
                      AND COALESCE(retry_count, 0) < :max_retries
                    """
                ),
                {"offset": f"-{int(stale_seconds)} seconds", "max_retries": max_retries},
            )
        await self.session.commit()
        return (result.rowcount or 0) + (dead.rowcount or 0)

    async def get_job(self, job_id: int) -> Optional[dict]:
        result = await self.session.execute(
            text(
                """
                SELECT id, tenant_id, invoice_id, status, claimed_by,
                       retry_count, last_error, available_at
                FROM jobs
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "tenant_id": row[1],
            "invoice_id": row[2],
            "status": row[3],
            "claimed_by": row[4],
            "retry_count": row[5],
            "last_error": row[6],
            "available_at": str(row[7]) if row[7] is not None else None,
        }

    async def heartbeat_job(self, job_id: int, worker_id: int) -> bool:
        result = await self.session.execute(
            text(
                """
                UPDATE jobs
                SET claimed_at = CURRENT_TIMESTAMP
                WHERE id = :job_id
                  AND status = 'claimed'
                  AND claimed_by = :worker_id
                """
            ),
            {"job_id": job_id, "worker_id": worker_id},
        )
        await self.session.commit()
        return (result.rowcount or 0) == 1

    async def retry_or_dead_letter_job(
        self,
        job_id: int,
        worker_id: int,
        error: str,
        max_retries: int,
        backoff_seconds: int,
    ) -> str:
        if self.dialect == "postgresql":
            available_expression = "CURRENT_TIMESTAMP + (:backoff_seconds * INTERVAL '1 second')"
        else:
            available_expression = "datetime('now', '+' || :backoff_seconds || ' seconds')"

        result = await self.session.execute(
            text(
                f"""
                UPDATE jobs
                SET retry_count = COALESCE(retry_count, 0) + 1,
                    status = CASE
                        WHEN COALESCE(retry_count, 0) + 1 >= :max_retries
                        THEN 'dead_letter'
                        ELSE 'pending'
                    END,
                    last_error = :error,
                    claimed_by = NULL,
                    claimed_at = NULL,
                    available_at = CASE
                        WHEN COALESCE(retry_count, 0) + 1 >= :max_retries
                        THEN available_at
                        ELSE {available_expression}
                    END,
                    completed_at = CASE
                        WHEN COALESCE(retry_count, 0) + 1 >= :max_retries
                        THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END
                WHERE id = :job_id
                  AND status = 'claimed'
                  AND claimed_by = :worker_id
                RETURNING status
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "error": error[:500],
                "max_retries": max_retries,
                "backoff_seconds": backoff_seconds,
            },
        )
        await self.session.commit()
        row = result.fetchone()
        if row is None:
            raise RuntimeError("Worker no longer owns this job")
        return row[0]

    async def get_execution_by_job(self, job_id: int) -> Optional[dict]:
        result = await self.session.execute(
            text(
                """
                SELECT tenant_id, id
                FROM executions
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return await self.get_execution(row[0], row[1])

    async def log_llm_call(
        self,
        tenant_id: int,
        execution_id: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        stop_reason: str,
        duration_ms: int,
        max_tokens: int,
    ) -> None:
        await self.session.execute(
            text(
                """
                INSERT INTO llm_calls (
                    tenant_id, execution_id, model, max_tokens,
                    completion_tokens, prompt_tokens, total_tokens,
                    stop_reason, duration_ms
                )
                VALUES (
                    :tenant_id, :execution_id, :model, :max_tokens,
                    :completion_tokens, :prompt_tokens, :total_tokens,
                    :stop_reason, :duration_ms
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "execution_id": execution_id,
                "model": model,
                "max_tokens": max_tokens,
                "completion_tokens": completion_tokens,
                "prompt_tokens": prompt_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "stop_reason": stop_reason,
                "duration_ms": duration_ms,
            },
        )
        await self.session.flush()

    async def replace_document_chunks(
        self,
        tenant_id: int,
        source_id: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        await self.session.execute(
            text(
                """
                DELETE FROM document_chunks
                WHERE tenant_id = :tenant_id AND source_id = :source_id
                """
            ),
            {"tenant_id": tenant_id, "source_id": source_id},
        )
        for chunk in chunks:
            vector = "[" + ",".join(f"{value:.8f}" for value in chunk["embedding"]) + "]"
            if self.dialect == "postgresql":
                statement = text(
                    """
                    INSERT INTO document_chunks (
                        tenant_id, source_id, chunk_index, content, metadata, embedding
                    )
                    VALUES (
                        :tenant_id, :source_id, :chunk_index, :content,
                        CAST(:metadata AS jsonb), CAST(:embedding AS vector)
                    )
                    """
                )
            else:
                statement = text(
                    """
                    INSERT INTO document_chunks (
                        tenant_id, source_id, chunk_index, content, metadata, embedding
                    )
                    VALUES (
                        :tenant_id, :source_id, :chunk_index, :content,
                        :metadata, :embedding
                    )
                    """
                )
            await self.session.execute(
                statement,
                {
                    "tenant_id": tenant_id,
                    "source_id": source_id,
                    "chunk_index": chunk["chunk_index"],
                    "content": chunk["content"],
                    "metadata": json.dumps(chunk["metadata"]),
                    "embedding": vector,
                },
            )
        await self.session.commit()
        return len(chunks)

    async def search_document_chunks(
        self,
        tenant_id: int,
        embedding: list[float],
        limit: int,
    ) -> list[dict[str, Any]]:
        vector = "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
        if self.dialect == "postgresql":
            result = await self.session.execute(
                text(
                    """
                    SELECT source_id, chunk_index, content, metadata,
                           1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                    FROM document_chunks
                    WHERE tenant_id = :tenant_id
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant_id, "embedding": vector, "limit": limit},
            )
            return [
                {
                    "source_id": row[0],
                    "chunk_index": row[1],
                    "content": row[2],
                    "metadata": row[3] if isinstance(row[3], dict) else json.loads(row[3]),
                    "similarity": float(row[4]),
                }
                for row in result.fetchall()
            ]

        result = await self.session.execute(
            text(
                """
                SELECT source_id, chunk_index, content, metadata, embedding
                FROM document_chunks
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )
        matches = []
        for row in result.fetchall():
            stored = [float(value) for value in row[4].strip("[]").split(",")]
            similarity = sum(left * right for left, right in zip(embedding, stored))
            matches.append(
                {
                    "source_id": row[0],
                    "chunk_index": row[1],
                    "content": row[2],
                    "metadata": json.loads(row[3]),
                    "similarity": similarity,
                }
            )
        matches.sort(key=lambda match: match["similarity"], reverse=True)
        return matches[:limit]

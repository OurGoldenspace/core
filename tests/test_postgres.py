"""Opt-in tests for behavior SQLite cannot prove.

Run against the Docker stack:
POSTGRES_TEST_ADMIN_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/workcore
POSTGRES_TEST_APP_URL=postgresql+asyncpg://workcore_app:workcore_app@localhost:5432/workcore
pytest tests/test_postgres.py -v
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.database import Database
from src.retrieval import ingest_document, search_documents

ADMIN_URL = os.getenv("POSTGRES_TEST_ADMIN_URL")
APP_URL = os.getenv("POSTGRES_TEST_APP_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not ADMIN_URL or not APP_URL,
        reason="Set POSTGRES_TEST_ADMIN_URL and POSTGRES_TEST_APP_URL",
    ),
]


def session_factory(url: str):
    engine = create_async_engine(url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_postgres_skip_locked_has_one_winner() -> None:
    suffix = uuid.uuid4().hex[:10]
    request_id = f"INV-PG-RACE-{suffix}"
    admin_engine, admin_factory = session_factory(ADMIN_URL)
    tenant_id = None
    try:
        async with admin_factory() as session:
            result = await session.execute(
                text(
                    """
                    INSERT INTO tenants (name, api_key)
                    VALUES (:name, :key)
                    RETURNING id
                    """
                ),
                {"name": f"pg-race-{suffix}", "key": f"pg-race-key-{suffix}"},
            )
            tenant_id = result.scalar_one()
            await session.execute(
                text(
                    """
                    INSERT INTO jobs (tenant_id, request_id, status)
                    VALUES (:tenant_id, :request_id, 'pending')
                    """
                ),
                {"tenant_id": tenant_id, "request_id": request_id},
            )
            await session.commit()

        async def claim(worker_id: int):
            async with admin_factory() as session:
                return await Database(session).claim_job(tenant_id, request_id, worker_id)

        results = await asyncio.gather(*[claim(worker_id) for worker_id in range(50)])
        assert len([job_id for job_id in results if job_id is not None]) == 1
    finally:
        if tenant_id is not None:
            async with admin_factory() as session:
                await session.execute(
                    text("DELETE FROM jobs WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await session.commit()
        await admin_engine.dispose()


async def test_postgres_rls_hides_other_tenant_rows() -> None:
    suffix = uuid.uuid4().hex[:10]
    admin_engine, admin_factory = session_factory(ADMIN_URL)
    app_engine, app_factory = session_factory(APP_URL)
    tenant_ids = []
    try:
        async with admin_factory() as session:
            for index in range(2):
                result = await session.execute(
                    text(
                        """
                        INSERT INTO tenants (name, api_key)
                        VALUES (:name, :key)
                        RETURNING id
                        """
                    ),
                    {
                        "name": f"rls-{suffix}-{index}",
                        "key": f"rls-key-{suffix}-{index}",
                    },
                )
                tenant_id = result.scalar_one()
                tenant_ids.append(tenant_id)
                await session.execute(
                    text(
                        """
                        INSERT INTO vendors (
                            tenant_id, vendor_id, name, is_approved, risk_level
                        )
                        VALUES (:tenant_id, 999, :name, true, 'low')
                        """
                    ),
                    {"tenant_id": tenant_id, "name": f"vendor-{suffix}-{index}"},
                )
            await session.commit()

        async with app_factory() as session:
            await Database(session).set_tenant_context(tenant_ids[0])
            result = await session.execute(
                text(
                    """
                    SELECT tenant_id
                    FROM vendors
                    WHERE tenant_id IN (:first, :second)
                    """
                ),
                {"first": tenant_ids[0], "second": tenant_ids[1]},
            )
            assert [row[0] for row in result.fetchall()] == [tenant_ids[0]]
    finally:
        if tenant_ids:
            async with admin_factory() as session:
                await session.execute(
                    text("DELETE FROM vendors WHERE tenant_id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                await session.execute(
                    text("DELETE FROM tenants WHERE id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                await session.commit()
        await app_engine.dispose()
        await admin_engine.dispose()


async def test_pgvector_search_is_tenant_scoped() -> None:
    suffix = uuid.uuid4().hex[:10]
    admin_engine, admin_factory = session_factory(ADMIN_URL)
    app_engine, app_factory = session_factory(APP_URL)
    tenant_ids = []
    try:
        async with admin_factory() as session:
            for index in range(2):
                result = await session.execute(
                    text(
                        """
                        INSERT INTO tenants (name, api_key)
                        VALUES (:name, :key)
                        RETURNING id
                        """
                    ),
                    {
                        "name": f"vector-{suffix}-{index}",
                        "key": f"vector-key-{suffix}-{index}",
                    },
                )
                tenant_ids.append(result.scalar_one())
            await session.commit()

        for index, tenant_id in enumerate(tenant_ids):
            async with app_factory() as session:
                db = Database(session)
                await db.set_tenant_context(tenant_id)
                await ingest_document(
                    db,
                    tenant_id,
                    f"tenant-{index}-document",
                    "oranges citrus procurement policy",
                    {"tenant_index": index},
                )

        async with app_factory() as session:
            db = Database(session)
            await db.set_tenant_context(tenant_ids[0])
            matches = await search_documents(
                db,
                tenant_ids[0],
                "oranges procurement",
                limit=10,
            )
            assert [match["source_id"] for match in matches] == ["tenant-0-document"]
    finally:
        if tenant_ids:
            async with admin_factory() as session:
                await session.execute(
                    text("DELETE FROM document_chunks WHERE tenant_id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                await session.execute(
                    text("DELETE FROM tenants WHERE id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                await session.commit()
        await app_engine.dispose()
        await admin_engine.dispose()

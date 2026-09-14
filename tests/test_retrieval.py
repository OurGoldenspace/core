"""Chunking, vector similarity, replacement, and tenant scope."""

from __future__ import annotations

from sqlalchemy import text

from src.database import Database
from src.retrieval import chunk_text, ingest_document, search_documents

AUTH = {"Authorization": "Bearer test-key-12345"}


def test_chunk_text_respects_size_and_overlap() -> None:
    chunks = chunk_text(
        "alpha beta gamma delta epsilon zeta eta theta iota kappa",
        chunk_chars=24,
        overlap_chars=6,
    )
    assert len(chunks) >= 3
    assert all(len(chunk) <= 24 for chunk in chunks)
    assert "alpha" in chunks[0]


async def test_retrieval_ranks_matching_document_first(seeded_session) -> None:
    db = Database(seeded_session)
    tenant = await db.get_tenant_by_api_key("test-key-12345")
    await ingest_document(
        db,
        tenant["id"],
        "vendor-policy",
        "oranges oranges citrus supplier approval and fruit purchasing",
        {"kind": "policy"},
    )
    await ingest_document(
        db,
        tenant["id"],
        "engineering-notes",
        "quantum compiler runtime bytecode and distributed tracing",
        {"kind": "notes"},
    )

    results = await search_documents(
        db,
        tenant["id"],
        "oranges citrus supplier",
        limit=2,
    )
    assert results[0]["source_id"] == "vendor-policy"
    assert results[0]["metadata"] == {"kind": "policy"}
    assert results[0]["similarity"] > results[1]["similarity"]


async def test_ingestion_replaces_source_atomically(seeded_session) -> None:
    db = Database(seeded_session)
    tenant = await db.get_tenant_by_api_key("test-key-12345")
    await ingest_document(db, tenant["id"], "replace-me", "old content apples", {})
    await ingest_document(db, tenant["id"], "replace-me", "new content bananas", {})
    result = await seeded_session.execute(
        text(
            """
            SELECT content
            FROM document_chunks
            WHERE tenant_id = :tenant_id AND source_id = 'replace-me'
            """
        ),
        {"tenant_id": tenant["id"]},
    )
    contents = [row[0] for row in result.fetchall()]
    assert contents == ["new content bananas"]


async def test_retrieval_is_tenant_scoped(seeded_session) -> None:
    db = Database(seeded_session)
    first = await db.get_tenant_by_api_key("test-key-12345")
    result = await seeded_session.execute(
        text(
            """
            INSERT INTO tenants (name, api_key)
            VALUES ('other-tenant', 'other-key')
            RETURNING id
            """
        )
    )
    second_id = result.scalar_one()
    await seeded_session.commit()

    await ingest_document(db, first["id"], "first-doc", "private alpha handbook", {})
    await ingest_document(db, second_id, "second-doc", "private alpha secrets", {})
    results = await search_documents(db, first["id"], "private alpha", limit=10)
    assert {item["source_id"] for item in results} == {"first-doc"}


async def test_retrieval_api_round_trip(client) -> None:
    created = await client.post(
        "/documents",
        headers=AUTH,
        json={
            "source_id": "api-policy",
            "content": "Invoices over five thousand dollars require human approval.",
            "metadata": {"department": "finance"},
        },
    )
    assert created.status_code == 201
    assert created.json()["chunks_written"] == 1

    response = await client.post(
        "/retrieve",
        headers=AUTH,
        json={"query": "human approval invoices", "limit": 3},
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["source_id"] == "api-policy"


async def test_retrieval_rejects_blank_query(client) -> None:
    response = await client.post(
        "/retrieve",
        headers=AUTH,
        json={"query": "   "},
    )
    assert response.status_code == 422

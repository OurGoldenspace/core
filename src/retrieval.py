"""Chunking and embeddings with a real pgvector storage path.

The deterministic embedder is a local/CI stand-in, not a semantic model.
The storage and provider boundary stay identical when a hosted embedding
provider replaces `embed_text`.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from src.config import settings
from src.database import Database

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def chunk_text(
    text: str,
    chunk_chars: int,
    overlap_chars: int,
) -> list[str]:
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= chunk_chars:
        raise ValueError("overlap_chars must be between zero and chunk_chars")

    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("document content cannot be blank")

    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_chars, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap_chars, start + 1)
        while start < len(normalized) and normalized[start] == " ":
            start += 1
    return chunks


def embed_text(text: str, dimensions: int | None = None) -> list[float]:
    """
    Deterministic signed feature hashing for local tests.

    This is deliberately not described as a semantic embedding model.
    """
    dimensions = dimensions or settings.EMBEDDING_DIMENSIONS
    if dimensions <= 0:
        raise ValueError("embedding dimensions must be positive")

    tokens = TOKEN_PATTERN.findall(text.lower())
    if not tokens:
        raise ValueError("text must contain searchable characters")

    vector = [0.0] * dimensions
    features = tokens + [f"{left}:{right}" for left, right in zip(tokens, tokens[1:])]
    for feature in features:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]


async def ingest_document(
    db: Database,
    tenant_id: int,
    source_id: str,
    content: str,
    metadata: dict[str, Any],
) -> int:
    text_chunks = chunk_text(
        content,
        chunk_chars=settings.RETRIEVAL_CHUNK_CHARS,
        overlap_chars=settings.RETRIEVAL_CHUNK_OVERLAP_CHARS,
    )
    chunks = [
        {
            "chunk_index": index,
            "content": chunk,
            "metadata": metadata,
            "embedding": embed_text(chunk),
        }
        for index, chunk in enumerate(text_chunks)
    ]
    return await db.replace_document_chunks(tenant_id, source_id, chunks)


async def search_documents(
    db: Database,
    tenant_id: int,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    matches = await db.search_document_chunks(
        tenant_id=tenant_id,
        embedding=embed_text(query),
        limit=min(limit * 3, 60),
    )
    return [match for match in matches if match["similarity"] > 0.05][:limit]

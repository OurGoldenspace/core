# Retrieval contract

## Production storage path

Alembic revision `0003` installs pgvector and creates `document_chunks` with:

- `vector(64)` embeddings
- HNSW cosine index
- `UNIQUE(tenant_id, source_id, chunk_index)`
- forced tenant RLS
- JSONB metadata

`POST /documents` replaces every chunk for one source in one transaction. `POST /retrieve` performs tenant-scoped cosine search.

## Local provider

`embed_text()` uses deterministic signed feature hashing. This is useful for repeatable CI and proving storage, ranking, tenancy, and context assembly. It is not presented as a semantic embedding model.

A production provider replaces only `embed_text()` and keeps the storage interface. Changing dimensions requires a migration because pgvector dimensions are part of the column type.

## Agent context

Before the model loop starts, the runtime retrieves up to three relevant chunks using vendor, department, and policy terms. Retrieved text is enclosed in `<retrieved_data>` and labeled untrusted. It participates in the same context-token budget as the rest of the transcript.

Retrieved text never overrides:

- Pydantic tool boundaries
- vendor approval from Postgres
- budget results
- duplicate detection
- the human approval threshold

`test_retrieved_prompt_injection_cannot_override_tools` stores a malicious document asking the agent to bypass validation. The blocked vendor remains rejected.

## Failure policy

Invoice processing is fail-open for retrieval availability but fail-closed for consequential decisions:

- If retrieval is unavailable, the runtime logs a warning and continues with authoritative tools.
- If final model JSON is invalid, the invoice goes to `needs_review`.
- Retrieved text cannot invoke a tool directly.

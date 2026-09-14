# Architecture (the contract)

Written before the extra surfaces were added. If code and this document disagree, the document is wrong until a review updates it.

This repo is a WorkCore-shaped invoice agent: a FastAPI service over Postgres (SQLite locally), a custom ReACT loop, and a database that enforces exactly-once work.

## What a request does

1. Bearer API key maps to a `tenants` row. Every later query includes `tenant_id`.
2. `ProcessInvoiceRequest` is validated. `invoice_id` is a tight character class so a model or client cannot smuggle a second prompt through the primary key.
3. `UNIQUE(tenant_id, idempotency_key)` binds a request key to one payload. Reusing the key with different data returns `409`.
4. `UNIQUE(job_id)` also makes the invoice identity converge on one execution when two clients use different request keys.
5. The database inserts the execution with `ON CONFLICT DO NOTHING`. Only the caller that receives the inserted row is the execution owner; concurrent callers wait for that row to leave `running`.
6. The owner claims the `jobs` row with `FOR UPDATE SKIP LOCKED` on Postgres, or `UPDATE ... WHERE status = 'pending'` on SQLite. A caller that did not claim the job cannot run the agent.
7. The agent loop asks the model (or the deterministic policy stand-in) what to do.
8. Tenant-scoped pgvector retrieval adds up to three chunks to the token-budgeted prompt. Retrieved text is explicitly untrusted.
9. Tool inputs, tool outputs, and the model's final JSON decision are validated with Pydantic. Invalid final output fails closed to `needs_review`.
10. Duplicate detection compares the submitted `invoice_date`, not request arrival time.
11. Every LLM call and every tool call is written before the HTTP response returns.
12. Amounts at or above `$5000` stop in `awaiting_review`. One reviewer atomically moves the execution to `reviewing`; every competing reviewer gets `409`.
13. Payment first reserves one durable `payments` row. The invoice-scoped idempotency key is what a real payment provider would receive, so a timeout can replay the same provider operation safely.

## Background job state machine

`POST /invoice-jobs` persists the execution payload and returns `202`; it does not run the model. `python -m src.worker` performs:

`pending → claimed → completed`

Branches:

- `claimed → awaiting_review → completed` for consequential payments.
- `claimed → pending` after a retryable failure, with exponential `available_at` backoff.
- `claimed → dead_letter` when the retry budget is exhausted.
- stale `claimed` jobs return to `pending`; stale jobs already at the retry limit move to `dead_letter`.

The claim transaction ends immediately after `claimed_by` and `claimed_at` are committed. Model and tool I/O do not hold a row lock. A separate session updates the heartbeat while the slow model call is in flight.

## Mapping to the role

| Job requirement | Where it lives |
| --- | --- |
| Custom loop, no agent framework | `src/agent.py` |
| Context inside a token budget | `src/context.py` `trim_messages` |
| Validate model output at the boundary | `src/models.py`, `src/tools.py` `execute_tool` |
| Parallel tools while a 12s model is in flight | `asyncio.gather` in `src/agent.py` |
| Stream while work is in flight | SSE lifecycle events plus Anthropic `model_delta` chunks |
| Multi-tenant REST + Postgres | `src/app.py`, `migrations/` |
| Database-enforced tenant isolation | Alembic RLS policy + transaction-local tenant context |
| Schema migrations | `migrations/`, validated as offline SQL and in PostgreSQL CI |
| Background processing | `POST /invoice-jobs`, `src/worker.py` |
| Atomic claim, heartbeat, backoff, dead letter | queue methods in `src/database.py` |
| Constraints over application hope | unique request key, job execution, invoice, and payment keys |
| Durable payment idempotency | `payments`, `reserve_payment`, invoice-scoped provider key |
| Reconstruct what happened and why | `executions`, `tool_invocations`, `llm_calls` |
| Person in control of consequential actions | `needs_review` + human approve/reject |
| Evals for a prompt change | `promptfooconfig.yaml`, `evals/` |
| Chunking, embeddings, vector search | `src/retrieval.py`, Alembic revision `0003` |
| Retrieval prompt-injection boundary | untrusted context delimiter + adversarial test |
| Concurrent tests that are actually concurrent | `tests/test_exactly_once.py`, `tests/test_concurrency.py` |
| Untrusted invoice text | invoice_id pattern; promptfoo jailbreak case |

## LLM policy

`ANTHROPIC_API_KEY` unset or placeholder → policy LLM that emits the same first-turn parallel tools and the `$5000` rule. That is how tests and the CI promptfoo baseline stay deterministic. The loop, validation, claiming, and audit trail are the same code path.

The promptfoo provider passes its candidate prompt into `InvoiceAgent.system_prompt`; it does not evaluate a disconnected text file. Prompt wording comparisons require `ANTHROPIC_API_KEY`. The policy baseline proves orchestration and safety invariants, not language quality.

## What we did not pretend to finish

- Hosted semantic embeddings. The provider boundary is real; local/CI vectors use deterministic feature hashing and are labeled accordingly.
- Email, files, and notifications. Out of scope for this demo.

## Decisions that are not optional

- Tool arguments are never passed to SQL without Pydantic.
- Tool results are validated too; trusted code can still violate its contract.
- Payment is not a side effect of a model sentence. It is a durable intent, and above threshold it requires one human reviewer.
- “Exactly once” means one local execution and one payment intent. Across an external payment API, correctness requires replaying our stable key against a provider that implements idempotency; a distributed transaction is not claimed.
- A claimed job whose heartbeat is stale returns to `pending` and increments `retry_count`.
- A promptfoo case that starts failing is a regression, not a flaky model.

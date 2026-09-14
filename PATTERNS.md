# Patterns

Three implementation details worth walking through in an interview.

## 1. Parallel tool execution

`validate_vendor`, `check_budget`, and `detect_duplicates` do not depend on each other. The first ReACT turn therefore emits all three tool calls and the loop runs:

```python
observations = await asyncio.gather(*[invoke(call) for call in turn.tool_calls])
```

`tests/test_concurrency.py` uses the same shape with 200ms / 150ms / 80ms sleeps. Sequential cost is the sum. Parallel cost is the longest call.

## 2. Exactly-once job claiming

Fifty workers must not process the same invoice twice.

Postgres:

```sql
UPDATE jobs
SET status = 'claimed', claimed_by = :worker_id, claimed_at = NOW()
WHERE id = (
    SELECT id FROM jobs
    WHERE status = 'pending'
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id
```

`SKIP LOCKED` lets waiters take the next free row instead of blocking on a row another worker already has.

SQLite has no `SKIP LOCKED`, so the claim is still a single `UPDATE ... WHERE status = 'pending'`. Only one transaction can flip a given row. `tests/test_exactly_once.py` starts 50 workers against 20 jobs and asserts 20 distinct claims.

## 3. Input validation at the tool boundary

Tools do not trust the model. Each handler validates with Pydantic first (`ValidateVendorInput`, `CheckBudgetInput`, ...). Negative amounts, unknown vendor types, and missing dates fail before any SQL runs.

HTTP input uses the same idea: `InvoiceRequest` rejects blank ids and non-positive amounts at the API edge.

## 4. Idempotency

`executions` has `UNIQUE (tenant_id, idempotency_key)`. The handler reads that row first. A retry with the same key returns the stored decision and `cached: true` instead of running the agent again.

A racing double-insert hits `IntegrityError`, rolls back, and re-reads the winner.

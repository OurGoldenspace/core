# ADR 001: Effectively-once PMS work order

Status: accepted

## Context

A client can retry, two workers can race, and AppFolio/Yardi can time out after accepting a work order. Postgres cannot atomically commit with an external PMS.

## Decision

One operation at each boundary:

1. `UNIQUE(tenant_id, idempotency_key)` — one execution per request key.
2. `UNIQUE(job_id)` — one execution per maintenance request, even with different keys.
3. The successful execution insert is the ownership token. Non-owners wait; they never run tools.
4. `UNIQUE(tenant_id, request_id)` on `work_orders` — one durable PMS write intent.
5. `work-order:{tenant_id}:{request_id}` is the stable key sent to the PMS adapter.
6. Human review uses an atomic conditional update; a second approver gets `409`.

The production adapter must send that key to a PMS that honors idempotency.

## Failure behavior

- Crash before reservation: retry can reserve and submit.
- Crash after reservation, before PMS: retry reuses the same key.
- Timeout after the PMS accepted: replay the same key, get the original work order.
- Concurrent retries: one execution owner.
- Concurrent human approvals: one `reviewing` transition.

## Consequences

Local tests prove one execution and one work-order row. End-to-end correctness still depends on the PMS honoring the key. We do not claim a distributed transaction.

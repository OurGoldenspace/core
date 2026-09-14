# ADR 001: Effectively-once invoice payment

Status: accepted

## Context

An HTTP client can retry, two workers can race, and a payment provider can time out after accepting a charge. No application can atomically commit both its Postgres transaction and an unrelated provider transaction without a distributed protocol.

Calling this “exactly once” without defining the boundary would be misleading.

## Decision

We enforce one operation at each boundary:

1. `UNIQUE(tenant_id, idempotency_key)` gives one execution per request identity.
2. `UNIQUE(job_id)` gives one execution per invoice identity, even when request keys differ.
3. The successful execution insert is the ownership token. Non-owners wait; they never run tools.
4. `UNIQUE(tenant_id, invoice_id)` on `payments` gives one durable payment intent.
5. `payment:{tenant_id}:{invoice_id}` is the stable provider idempotency key.
6. A human-review transition uses an atomic conditional update, so only one reviewer can initiate payment.

The production payment adapter must send the stable key to a provider with idempotent request support. Retrying after an ambiguous timeout then returns the original provider transaction.

## Failure behavior

- Crash before payment reservation: retry can reserve and submit.
- Crash after reservation but before provider call: retry reuses the reservation and stable key.
- Timeout after provider accepted payment: retry sends the same key and retrieves the original charge.
- Concurrent retries: one execution owner; all other callers observe its result.
- Concurrent human approvals: one `reviewing` transition; other reviewers receive `409`.

## Consequences

- The database contains a reconstructable payment intent before external I/O.
- Local tests can prove one execution and one payment row.
- End-to-end correctness depends on the external provider honoring idempotency. We state that dependency explicitly rather than claiming an impossible cross-system atomic commit.

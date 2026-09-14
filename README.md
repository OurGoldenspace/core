# WorkCore Invoice Agent Demo

**AI-Powered Invoice Processing System**

Production-grade implementation demonstrating core patterns for agentic AI infrastructure.

---

## 🎯 30-Second Pitch

**What this is:**
Invoice processor with a ReACT agent that validates vendors, checks budgets, detects duplicates, and makes approval decisions. Built with production patterns: exactly-once execution, async concurrency, tool validation, comprehensive audit logging.

**What you'll see:**
- Single invoice arrives
- Agent reasons through 3 parallel validation tools
- Database prevents race conditions atomically
- Every decision logged for compliance
- Approval in ~2 seconds (not blocking other users)

**Why it matters:**
This is exactly what WorkCore needs: reliable agent infrastructure that handles concurrency, ensures data consistency, validates untrusted LLM output, and maintains audit trails.

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────┐
│  USER: POST /process-invoice                │
│  Invoice: {id, vendor_id, amount, date}     │
└────────────────┬────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────┐
│  FASTAPI ENDPOINT                           │
│  1. Validate request (Pydantic)             │
│  2. Check idempotency (exactly-once)        │
│  3. Create job in queue                     │
│  4. Create execution record                 │
└────────────────┬────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────┐
│  AGENT LOOP (ReACT Pattern)                 │
│                                             │
│  Iteration 1:                               │
│  ├─ Call LLM: "What should I do?"          │
│  ├─ LLM: "I'll validate & check budget"    │
│  ├─ Parallel Execution:                     │
│  │  ├─ validate_vendor()      (234ms)      │
│  │  ├─ check_budget()         (156ms)      │
│  │  └─ detect_duplicates()    (89ms)       │
│  │  Total: ~234ms (not 479ms)               │
│  │                                          │
│  ├─ Log to audit trail                      │
│  └─ Feed results back to LLM                │
│                                             │
│  Iteration 2:                               │
│  ├─ LLM reads: "All checks passed"         │
│  ├─ LLM decides: "Approve" or "Review"     │
│  └─ Return final decision                   │
└────────────────┬────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────┐
│  DATABASE (PostgreSQL)                      │
│                                             │
│  ✅ FOR UPDATE SKIP LOCKED                  │
│     → Atomic job claiming (no duplicates)   │
│                                             │
│  ✅ UNIQUE Constraints                      │
│     → Idempotency (same key = cached)       │
│                                             │
│  ✅ Audit Trail Table                       │
│     → Every tool call logged                │
│     → Timestamps, inputs, outputs           │
│     → Auditors can reconstruct everything   │
└─────────────────────────────────────────────┘
```

---

## 🔑 Core Patterns Demonstrated

### Pattern #1: Effectively-Once Execution and Payment

**Problem:** Network fails, user retries → invoice processed twice → customer charged twice ❌

**Solution:** database-selected ownership + durable payment idempotency

```python
# Request identity: retries converge
UNIQUE(tenant_id, idempotency_key)

# Invoice identity: different request keys still converge
UNIQUE(job_id)

# Side-effect identity: one durable payment intent
UNIQUE(tenant_id, invoice_id) ON payments

# Stable key sent to an idempotent payment provider
payment:{tenant_id}:{invoice_id}
```

**Test:** `pytest tests/test_exactly_once.py`
- 10 concurrent HTTP retries → one execution and one payment
- Same invoice with two request keys → one execution
- 50 workers race for 20 jobs → 20 distinct claims

The project does not claim an impossible atomic commit across Postgres and an external provider. It guarantees one local intent and requires the provider to honor the stable idempotency key. See `docs/adr/001-exactly-once-payments.md`.

### Pattern #2: Async Concurrency

**Problem:** Model calls take 12 seconds. 10 users = 120 seconds ❌

**Solution:** asyncio.gather() for parallel execution

```python
# Sequential (BAD):
await validate_vendor()      # 234ms
await check_budget()         # 156ms
await detect_duplicates()    # 89ms
# Total: 479ms

# Parallel (GOOD):
await asyncio.gather(
    validate_vendor(),       # starts now
    check_budget(),          # starts now
    detect_duplicates()      # starts now
)
# Total: ~234ms (max of the three)

# 10 concurrent invoices:
# Sequential: 10 × 479ms = 4790ms
# Parallel:   ~479ms (all run concurrently)
```

**Test:** `pytest tests/test_concurrency.py`
- 10 concurrent invoice requests
- Measure total latency (should stay constant)

### Pattern #3: Tool Validation

**Problem:** LLM might ask to "delete_database" or send invalid data ❌

**Solution:** Pydantic validates BEFORE execution

```python
# LLM tries:
tool_call = {
    "name": "validate_vendor",
    "input": {"vendor_id": "invalid"}  # Should be int
}

# Pydantic validation:
class ValidateVendorInput(BaseModel):
    vendor_id: int = Field(..., gt=0, lt=1_000_000)

# Result:
ValidationError: "vendor_id must be integer"
# Tool NEVER executes
# Error sent back to LLM
```

**Test:** `pytest tests/test_validation.py`
- Invalid inputs rejected
- Tool never executes
- Error returned to agent

### Pattern #4: Audit Trail

**Problem:** Auditors ask "What happened?" - need proof ❌

**Solution:** Log every tool call to database

```
tool_invocations table:

execution_id | tool_name        | tool_input              | tool_result        | duration_ms
─────────────┼──────────────────┼─────────────────────────┼────────────────────┼────────────
42           | validate_vendor  | {vendor_id: 1}         | {is_approved: true}| 234
42           | check_budget     | {dept_id: 1, amt: 5000}| {has_budget: true} | 156
42           | detect_duplicates| {vendor_id: 1, ...}    | {is_duplicate: no} | 89
```

**Later: Auditors can reconstruct everything:**
"What happened with invoice INV-001?"
→ Query execution_id = 42
→ Show 3 tool calls, inputs, outputs, latencies
→ Prove nothing was tampered with ✅

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Docker is optional (Postgres interview path). Local default is SQLite.

### 1. Setup Environment

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python data/generate_data.py
python -m src.seed
```

No hosted-model key is required. With both provider keys empty, the
deterministic policy fallback exercises the ReACT loop, tools, and audit trail.

Groq is the preferred free-tier provider. Add your key only to `.env`:

```env
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=openai/gpt-oss-20b
```

`LLM_PROVIDER=auto` selects Groq first, Anthropic second, and policy last.
Keys are never committed because `.env` is ignored. Restart the API and worker
after changing provider settings.

### 2. Start the API

```bash
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
```

For the durable background path, start another terminal:

```bash
python -m src.worker
```

Submit work with `POST /invoice-jobs`, then poll `GET /invoice-jobs/{job_id}`. This path returns `202` before the model runs. The worker maintains a heartbeat, applies exponential retry backoff, and moves exhausted work to `dead_letter`.

Postgres + Docker remains the interview path:

```bash
docker-compose up -d
```

Compose runs Alembic with the migration role, seeds with that role, then starts the API as restricted `workcore_app`. PostgreSQL RLS is therefore active in the demo. See `docs/DATABASE.md`.

### 3. Test Health

```bash
curl http://localhost:8000/health
# Response: {"status": "healthy", "timestamp": "..."}
```

### 4. Process an Invoice

```bash
curl -X POST http://localhost:8000/process-invoice \
  -H "Authorization: Bearer test-key-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "invoice_id": "INV-2024-00001",
    "vendor_id": 1,
    "vendor_name": "Acme Corp Supplies",
    "department_id": 1,
    "amount": 2500.00,
    "date": "2024-09-13",
    "idempotency_key": "unique-key-12345"
  }'
```

**Response:**
```json
{
  "execution_id": 1,
  "invoice_id": "INV-2024-00001",
  "decision": "approved",
  "reason": "Vendor approved, budget available, no duplicates. Amount $2500 < threshold $5000 → auto-approved",
  "iterations": 2,
  "tokens_used": 847,
  "duration_ms": 2341,
  "cached": false
}
```

Other surfaces that match the job listing:

- `POST /documents` and `POST /retrieve` — chunking, embeddings, and tenant-scoped vector search
- `POST /invoice-jobs` — enqueue durable background work
- `GET /invoice-jobs/{job_id}` — observe pending, claimed, review, or terminal state
- `POST /process-invoice/stream` — SSE lifecycle events and real Groq or Anthropic text deltas while work is in flight
- `POST /executions/{id}/approve` and `/reject` — a person confirms anything ≥ $5000
- `GET /executions/{id}` — reconstruct tools, inputs, and the decision
- `npx promptfoo@latest eval` — decide whether a prompt change got better or worse

---

## 🧪 Testing

### Run All Tests

```bash
pytest tests/ -v -s
```

### Test Patterns

**Exactly-Once (Must Pass):**
```bash
pytest tests/test_exactly_once.py -v
# Shows: 50 workers claim 20 jobs → exactly 20 claims, zero duplicates
```

**Concurrency (Must Pass):**
```bash
pytest tests/test_concurrency.py -v
# Shows: 10 concurrent invoices, latency stays constant
```

**Validation (Must Pass):**
```bash
pytest tests/test_validation.py -v
# Shows: Invalid inputs rejected, tools never execute
```

**Agent Loop (Must Pass):**
```bash
pytest tests/test_agent_loop.py -v
# Shows: Full workflow end-to-end
```

**Human review + worker death:**
```bash
pytest tests/test_hitl.py tests/test_reclaim.py tests/test_worker.py -v
```

**PostgreSQL-only behavior:**
```bash
pytest tests/test_postgres.py -v
# Real FOR UPDATE SKIP LOCKED race and cross-tenant RLS isolation
```

**Retrieval and prompt injection:**
```bash
pytest tests/test_retrieval.py tests/test_agent_loop.py -v
# Ranking, source replacement, tenant isolation, and malicious retrieved text
```

**promptfoo evals (decision contract):**
```bash
npx promptfoo@latest eval --no-cache
```

See `evals/README.md`. A prompt change that flips `rejected` → `approved` fails CI.

---

## 📊 Database Schema

### Tables

**tenants** - Multi-tenant isolation
- id, name, api_key

**vendors** - Reference data (validated)
- id, tenant_id, vendor_id, name, is_approved, risk_level, credit_limit

**departments** - Reference data (with budgets)
- id, tenant_id, dept_id, name, budget_annual, budget_spent, budget_available

**jobs** - Work queue (FOR UPDATE SKIP LOCKED)
- id, tenant_id, invoice_id, status, claimed_by, claimed_at, retry_count, last_error

**executions** - Request tracking + idempotency
- id, tenant_id, job_id, idempotency_key, invoice_date, state, decision, reason

**payments** - Durable side-effect intents
- id, tenant_id, execution_id, invoice_id, idempotency_key, transaction_id, status

**tool_invocations** - Audit trail (CRITICAL)
- id, tenant_id, execution_id, tool_name, tool_input, tool_result, duration_ms

**llm_calls** - LLM tracking
- id, tenant_id, execution_id, model, tokens, stop_reason, duration_ms

---

## 🎯 Interview Walkthrough

### Opening (30 seconds)

> "I built an invoice processor to understand your infrastructure challenges. 
> It demonstrates exactly what you're hiring for: async concurrency, database-selected
> execution ownership, durable payment idempotency, tool validation, and audit logging. Watch."

### Demo (5 minutes)

**Step 1: Show architecture** (1 min)
```
"Here's the system: FastAPI endpoints → agent loop → PostgreSQL with constraints"
```

**Step 2: Run curl command** (1 min)
```bash
curl -X POST http://localhost:8000/process-invoice \
  -H "Authorization: Bearer test-key-12345" \
  -H "Content-Type: application/json" \
  -d '{"invoice_id": "INV-001", "vendor_id": 1, "department_id": 1, "amount": 2500, "date": "2024-09-13"}'
```

**Step 3: Show results** (1 min)
```
"Processed in 2.3 seconds. Agent called 3 tools in parallel. 
All logged to audit trail. Decision: approved."
```

**Step 4: Deep-dive on ONE pattern** (2 min)

**Option A - Exactly-Once:**
```
"This is the hard part. A request can retry and a provider can time out
after accepting payment. Postgres and the provider cannot share one transaction.

The database selects one execution owner and stores one payment intent.
Retries replay a stable key to the payment provider.

Test: 10 simultaneous retries. Result: one execution and one payment row."
```

**Option B - Concurrency:**
```
"Model calls take 12 seconds. If I block, 10 users = 120 seconds.
I solved it with asyncio.gather() for parallel execution.

[Pull up code showing 3 tools in asyncio.gather]

Result: 3 tools run in parallel. Latency: ~234ms (max of the three), 
not 479ms (sum of the three)."
```

**Option C - Validation:**
```
"LLM is not a programmer. It might ask to delete the database 
or send invalid data. I solve it with Pydantic.

[Pull up Pydantic model]

Every tool call validated before execution. Invalid calls rejected, 
never executed. Error sent back to LLM."
```

### Questions They'll Ask

**Q: "How do you handle failures?"**
A: "Timeouts on LLM calls (30s max). Validation catches bad inputs. 
All errors logged. Agent can retry."

**Q: "How do you scale this?"**
A: "PostgreSQL handles concurrency. Kubernetes scales app servers. 
Load balancer distributes invoices. Same patterns work at 1000s of agents."

**Q: "Why not use LangChain?"**
A: "Frameworks hide too much. I need control over exactly-once semantics, 
tenant isolation, and audit logging. Custom loop gives me that."

---

## 📁 Project Structure

```
workcore-invoice-agent/
├── src/
│   ├── app.py                    FastAPI, SSE stream, human approve/reject
│   ├── agent.py                  ReACT agent loop (CORE)
│   ├── context.py                Token-budget assembly
│   ├── retrieval.py              Chunking, embeddings, cosine search
│   ├── tools.py                  Tool implementations
│   ├── database.py               Async SQL + SKIP LOCKED / reclaim
│   ├── worker.py                 Heartbeat, retries, and job execution
│   ├── models.py                 Pydantic validation
│   ├── config.py                 Configuration
│   └── schema.sql                Local/bootstrap schema reference
│
├── migrations/                   Alembic schema + PostgreSQL RLS
├── evals/                        promptfoo provider + assertions
├── promptfooconfig.yaml
├── ARCHITECTURE.md               Written contract
│
├── tests/
│   ├── test_exactly_once.py      50 workers → 20 claims
│   ├── test_concurrency.py       Parallel execution + live HTTP
│   ├── test_validation.py        Pydantic validation
│   ├── test_agent_loop.py        End-to-end
│   ├── test_hitl.py              Human review
│   ├── test_worker.py            Queue, retry, dead letter, heartbeat
│   ├── test_postgres.py          SKIP LOCKED + RLS (opt-in)
│   ├── test_retrieval.py         Ranking, tenancy, replacement
│   └── test_reclaim.py           Stale claim replay
│
├── data/
│   ├── generate_data.py          Synthetic data generator
│   ├── vendors.json              20 vendors
│   ├── departments.json          5 departments
│   └── sample_invoices.json      100 invoices
│
├── docker-compose.yml            PostgreSQL + FastAPI
├── Dockerfile                    Container image
├── requirements.txt              Python dependencies
├── .env.example                  Configuration template
└── README.md                     This file
```

---

## 🔗 Key Links in Code

**Exactly-Once Pattern:**
- `src/database.py` → `acquire_execution()` and `reserve_payment()`
- `migrations/versions/0001_initial_runtime.py` → request, job, and payment constraints
- `tests/test_exactly_once.py` → Proof test
- `docs/adr/001-exactly-once-payments.md` → Failure model and external boundary

**Concurrency:**
- `src/agent.py` → `_execute_and_log_tool()` method
- `src/agent.py` → `asyncio.gather(*tool_results)`
- `tests/test_concurrency.py` → Load test

**Validation:**
- `src/models.py` → Pydantic models
- `src/agent.py` → Tool execution with validation
- `tests/test_validation.py` → Edge cases

**Audit Trail:**
- `migrations/versions/0001_initial_runtime.py` → `tool_invocations` table
- `src/database.py` → `log_tool_invocation()` method
- Query: `SELECT * FROM tool_invocations WHERE execution_id = X`

---

## 💡 What This Proves

✅ **You understand production patterns**
- Exactly-once execution (prevents duplicates)
- Async concurrency (handles latency)
- Validation (safety)
- Audit logging (compliance)

✅ **You understand their pain**
- Long model latency
- Race conditions in job queues
- Untrusted LLM output
- Regulatory requirements

✅ **You've built something that works**
- End-to-end system
- Tests proving correctness
- Docker containerization
- Ready to run

---

## 🎤 Final Talking Point

> "WorkCore solves a real problem: reliable agentic AI infrastructure.
> This demo shows I understand that problem. I've built exactly the patterns
> you need: concurrency, consistency, validation, compliance.
> I know your challenges because I've implemented the solutions."

---

## 📞 Next Steps

1. **Run locally:** `docker-compose up`
2. **Process invoices:** Use curl or API client
3. **Run tests:** `pytest tests/ -v`
4. **Review code:** Start with `src/agent.py`
5. **Deep-dive:** Pick one pattern, master it
6. **Interview:** Tell the story of what you built

---

**Build date:** Sept 13, 2026  
**Status:** Interview-ready ✅  
**Tech:** FastAPI + PostgreSQL + Anthropic Claude + asyncio  
**Patterns:** Exactly-once, concurrency, validation, audit logging
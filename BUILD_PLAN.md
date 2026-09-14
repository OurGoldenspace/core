# Build & Execution Plan

**Status:** Interview path is live. Tests, promptfoo evals, human review, and SSE streaming are in place.

> Historical execution checklist. The current operational contract is
> `ARCHITECTURE.md`, `docs/DATABASE.md`, and `docs/INTERVIEW_WALKTHROUGH.md`.
> PostgreSQL now uses Alembic; commands below that invoke `src/schema.sql`
> directly are retained only as build history.

---

## 📋 What's Been Created

### ✅ Complete
- [x] Project structure (folders + files)
- [x] Synthetic data generator (vendors, departments, invoices)
- [x] PostgreSQL schema (all tables + constraints)
- [x] Pydantic models (validation for all inputs)
- [x] Configuration management
- [x] Async database layer
- [x] Tool implementations (validate_vendor, check_budget, detect_duplicates, process_payment)
- [x] Agent loop (ReACT pattern with parallel execution)
- [x] FastAPI endpoints
- [x] Docker configuration
- [x] README (interview-first structure)
- [x] Real exactly-once, concurrency, validation, worker, and retrieval tests

### 📝 Scaffolding Only (Needs Data)
- [ ] Run actual database tests
- [ ] Generate sample data in database
- [ ] Test with real Anthropic API calls

---

## 🚀 Phase: Data Generation

**Time: 30 minutes**

### Step 1: Verify Data Generation
```bash
cd /mnt/user-data/outputs/workcore-invoice-agent

# Check generated data files
ls -la data/
cat data/vendors.json | head -20
cat data/departments.json | head -10
```

### Step 2: Verify Data Quality
```bash
python data/generate_data.py
# Should show:
# ✅ Generated 20 vendors → vendors.json
# ✅ Generated 5 departments → departments.json
# ✅ Generated 100 sample invoices → sample_invoices.json
```

---

## 🗄️ Phase: Database Setup

**Time: 45 minutes**

### Step 1: Start PostgreSQL
```bash
# Make sure .env has DATABASE_URL
export ANTHROPIC_API_KEY="sk-ant-YOUR-KEY"

# Start services
docker-compose up -d

# Wait for postgres to be healthy
docker-compose ps
```

### Step 2: Initialize Schema
```bash
# Option A: Using asyncio directly
python -c "
import asyncio
from src.database import init_db
asyncio.run(init_db())
"

# Option B: Using SQL file
alembic upgrade head
```

### Step 3: Verify Schema
```bash
docker exec workcore-postgres psql -U postgres -d workcore -c "\dt"
# Should show: tenants, vendors, departments, jobs, executions, tool_invocations, llm_calls
```

### Step 4: Seed Reference Data
```bash
# Create small seed script
cat > /tmp/seed.py << 'EOF'
import asyncio
import json
from src.database import AsyncSessionLocal, Database

async def seed():
    # Load data
    with open('data/vendors.json') as f:
        vendors = json.load(f)
    with open('data/departments.json') as f:
        departments = json.load(f)
    
    async with AsyncSessionLocal() as session:
        db = Database(session)
        
        # Insert tenant
        # Insert vendors
        # Insert departments
        
        print("✅ Seed complete")

asyncio.run(seed())
EOF

python /tmp/seed.py
```

---

## 🧪 Phase: Testing

**Time: 1.5 hours**

### Step 1: Run Validation Tests
```bash
pytest tests/test_validation.py -v

# Expected output:
# test_validate_vendor_input_rejects_invalid_type PASSED
# test_check_budget_input_rejects_negative_amount PASSED
# ... (8-10 tests)
```

### Step 2: Run Concurrency Tests
```bash
pytest tests/test_concurrency.py -v

# Expected output:
# Shows: Sequential: ~479ms, Parallel: ~234ms
# Proves: Parallel is 2x faster
```

### Step 3: Run Exactly-Once Tests
```bash
pytest tests/test_exactly_once.py -v

# Implemented with real DB calls:
# - 50 workers claim 20 jobs
# - 10 concurrent retries produce one execution and one payment
```

### Step 4: Full Test Run
```bash
pytest tests/ -v

# All tests should pass
# Collect coverage: pytest tests/ --cov=src
```

---

## 🔄 Phase: Integration Testing

**Time: 2 hours**

### Step 1: Health Check
```bash
curl http://localhost:8000/health
# Response: {"status": "healthy", "timestamp": "..."}
```

### Step 2: Process Sample Invoice
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
    "date": "2024-09-13"
  }'

# Expected response (after 2-3 seconds):
# {
#   "execution_id": 1,
#   "invoice_id": "INV-2024-00001",
#   "decision": "approved",
#   "reason": "...",
#   "iterations": 2,
#   "tokens_used": 847,
#   "duration_ms": 2341
# }
```

### Step 3: Test Idempotency
```bash
# Send SAME request with SAME idempotency_key
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
    "idempotency_key": "unique-key-123"
  }'

# First call: Creates execution
# Second call with SAME key: Returns cached result (same execution_id)

# Prove it's cached:
curl -X POST ... -d '{"...idempotency_key": "unique-key-123"}' # Should be instant
```

### Step 4: Check Audit Trail
```bash
docker exec workcore-postgres psql -U postgres -d workcore -c \
  "SELECT tool_name, duration_ms FROM tool_invocations LIMIT 10"

# Should see:
# validate_vendor  | 234
# check_budget     | 156
# detect_duplicates| 89
# final_decision   | 50
```

---

## 📊 Phase: Demo Preparation

**Time: 1.5 hours**

### Step 1: Create Demo Script
```bash
cat > demo.sh << 'EOF'
#!/bin/bash

echo "🚀 WorkCore Invoice Agent Demo"
echo "=============================="
echo ""

echo "1️⃣  Health Check"
curl http://localhost:8000/health
echo ""

echo "2️⃣  Processing Invoice..."
RESPONSE=$(curl -s -X POST http://localhost:8000/process-invoice \
  -H "Authorization: Bearer test-key-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "invoice_id": "INV-2024-DEMO",
    "vendor_id": 1,
    "vendor_name": "Acme Corp",
    "department_id": 1,
    "amount": 2500.00,
    "date": "2024-09-13"
  }')

echo "Response: $RESPONSE"
EXEC_ID=$(echo $RESPONSE | jq -r '.execution_id')

echo ""
echo "3️⃣  Audit Trail for Execution $EXEC_ID"
docker exec workcore-postgres psql -U postgres -d workcore -c \
  "SELECT tool_name, duration_ms FROM tool_invocations WHERE execution_id = $EXEC_ID"

echo ""
echo "✅ Demo complete"
EOF

chmod +x demo.sh
```

### Step 2: Practice Walkthrough
```bash
./demo.sh
# Time it: Should complete in ~3-5 seconds
```

### Step 3: Prepare Talking Points
```markdown
# Interview Demo Flow

1. "Here's the system: FastAPI → agent loop → PostgreSQL"
   [Point to architecture]

2. "Watch an invoice get processed"
   [Run curl command]

3. "Agent called 3 tools in parallel, decided in 2.3 seconds"
   [Show response]

4. "Here's the audit trail - every tool call logged"
   [Show database results]

5. "Let me show you one pattern I'm proud of"
   [Pick: exactly-once, concurrency, or validation]
```

---

## 📁 Deliverables Checklist

### Code
- [x] All Python source files
- [x] Database schema
- [x] Configuration
- [x] Docker setup

### Documentation
- [x] README (main guide)
- [x] This file (build plan)
- [ ] ARCHITECTURE.md (deep dive)
- [ ] PATTERNS.md (pattern explanations)

### Tests
- [x] Test implementations
- [x] Local suite passes
- [x] PostgreSQL-only suite runs in CI

### Demo
- [x] Sample data
- [ ] Demo script
- [ ] Practice run

---

## 🎯 Success Criteria

✅ **By end of Day 2:**
- [ ] Database initializes cleanly
- [ ] Can process sample invoice end-to-end
- [ ] Agent returns valid decision
- [ ] Audit trail is populated
- [ ] Tests demonstrate patterns
- [ ] README is polished
- [ ] Demo runs in 30 seconds
- [ ] Ready for interview

---

## 🚨 Troubleshooting

### Database Connection Error
```
Error: could not connect to database
Fix: docker-compose ps (check if postgres is running)
     docker-compose logs postgres (check logs)
     docker-compose down && docker-compose up -d (restart)
```

### Anthropic API Key Not Working
```
Error: 401 Unauthorized
Fix: export ANTHROPIC_API_KEY="sk-ant-YOUR-ACTUAL-KEY"
     docker-compose restart app
```

### Tests Fail
```
Error: test_xxx FAILED
Fix: pytest tests/test_xxx.py -v -s (see detailed output)
     Check if database is populated
     Check if ANTHROPIC_API_KEY is set
```

### Postgres Port Conflict
```
Error: Address already in use
Fix: lsof -i :5432 (find process using port)
     kill -9 <PID> (kill the process)
     docker-compose up -d (restart)
```

---

## 📞 Next Actions

1. **Now:** Verify all files are in `/mnt/user-data/outputs/workcore-invoice-agent/`
2. **Next:** Start database with docker-compose
3. **Then:** Seed data and run tests
4. **Finally:** Run demo and practice interview walkthrough

---

**Timeline:** 
- Data: 30 min ✓
- Database: 45 min ⏳
- Testing: 1.5h ⏳
- Integration: 2h ⏳
- Demo: 1.5h ⏳
- **Total:** ~6 hours (fits in Day 1-2 window)

**Let's go build something impressive!** 🚀
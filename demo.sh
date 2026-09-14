#!/usr/bin/env bash
set -euo pipefail

echo "WorkCore Invoice Agent Demo"
echo "==========================="
echo ""

echo "1. Health Check"
curl -s http://localhost:8000/health
echo ""
echo ""

echo "2. Processing Invoice..."
RESPONSE=$(curl -s -X POST http://localhost:8000/process-invoice \
  -H "Authorization: Bearer test-key-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "invoice_id": "INV-2024-DEMO",
    "vendor_id": 1,
    "vendor_name": "Acme Corp Supplies",
    "department_id": 1,
    "amount": 2500.00,
    "date": "2024-09-13"
  }')

echo "Response: $RESPONSE"
EXEC_ID=$(python -c "import json,sys; print(json.loads(sys.argv[1])['execution_id'])" "$RESPONSE")

echo ""
echo "3. Audit Trail for Execution $EXEC_ID"
curl -s "http://localhost:8000/executions/$EXEC_ID" \
  -H "Authorization: Bearer test-key-12345"
echo ""
echo ""
echo "Demo complete"

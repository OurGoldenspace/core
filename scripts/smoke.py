import json
import urllib.request
from datetime import datetime, timezone


def req(method: str, url: str, data: dict | None = None, headers: dict | None = None) -> dict:
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode())


def main() -> None:
    health = req("GET", "http://127.0.0.1:8000/health")
    print("HEALTH", json.dumps(health))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    invoice = {
        "invoice_id": f"INV-LIVE-{run_id}",
        "vendor_id": 1,
        "vendor_name": "Acme Corp Supplies",
        "department_id": 1,
        "amount": 2500.00,
        "date": "2024-09-13",
        "idempotency_key": f"live-key-{run_id}",
    }
    headers = {"Authorization": "Bearer test-key-12345"}
    first = req("POST", "http://127.0.0.1:8000/process-invoice", invoice, headers)
    print("FIRST", json.dumps(first))
    second = req("POST", "http://127.0.0.1:8000/process-invoice", invoice, headers)
    print("SECOND", json.dumps(second))
    audit = req("GET", f"http://127.0.0.1:8000/executions/{first['execution_id']}", headers=headers)
    print("TOOLS", [item["tool_name"] for item in audit["tools"]])


if __name__ == "__main__":
    main()

$ErrorActionPreference = "Stop"

Write-Host "WorkCore Invoice Agent Demo"
Write-Host "==========================="
Write-Host ""

Write-Host "1. Health Check"
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json -Compress
Write-Host ""

Write-Host "2. Processing Invoice..."
$body = @{
    invoice_id = "INV-2024-DEMO"
    vendor_id = 1
    vendor_name = "Acme Corp Supplies"
    department_id = 1
    amount = 2500.00
    date = "2024-09-13"
} | ConvertTo-Json

$response = Invoke-RestMethod -Method Post `
    -Uri http://localhost:8000/process-invoice `
    -Headers @{ Authorization = "Bearer test-key-12345" } `
    -ContentType "application/json" `
    -Body $body

$response | ConvertTo-Json -Compress
$execId = $response.execution_id

Write-Host ""
Write-Host "3. Audit Trail for Execution $execId"
Invoke-RestMethod -Uri "http://localhost:8000/executions/$execId" `
    -Headers @{ Authorization = "Bearer test-key-12345" } | ConvertTo-Json -Depth 6

Write-Host ""
Write-Host "Demo complete"

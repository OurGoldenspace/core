"""
Test Async Concurrency

This is THE TEST for asyncio.gather() parallelization.

Problem: 3 tools each take ~234ms.
- Sequential: 234 + 156 + 89 = 479ms
- Parallel: max(234, 156, 89) = 234ms

With 10 concurrent invoices:
- Sequential: 10 × 479 = 4790ms (almost 5 seconds!)
- Parallel: 479ms (only one invoice's latency)

This test measures both scenarios.
"""

import pytest
import asyncio
import time
from decimal import Decimal


@pytest.mark.asyncio
async def test_parallel_tool_execution():
    """
    Test that tools run in parallel, not sequentially.
    
    Simulates:
    - validate_vendor: 234ms
    - lookup_unit: 156ms
    - detect_open_work_orders: 89ms
    
    Parallel should take ~234ms (max), not ~479ms (sum).
    """
    
    async def tool_1():
        """Simulate validate_vendor (234ms)."""
        await asyncio.sleep(0.234)
        return "approved"
    
    async def tool_2():
        """Simulate lookup_unit (156ms)."""
        await asyncio.sleep(0.156)
        return "has_budget"
    
    async def tool_3():
        """Simulate detect_open_work_orders (89ms)."""
        await asyncio.sleep(0.089)
        return "not_duplicate"
    
    # Sequential (BAD)
    start = time.time()
    result1 = await tool_1()
    result2 = await tool_2()
    result3 = await tool_3()
    sequential_ms = (time.time() - start) * 1000
    
    # Parallel (GOOD)
    start = time.time()
    results = await asyncio.gather(tool_1(), tool_2(), tool_3())
    parallel_ms = (time.time() - start) * 1000
    
    # Assertions
    print(f"Sequential: {sequential_ms:.0f}ms")
    print(f"Parallel: {parallel_ms:.0f}ms")
    
    # Sequential should be ~479ms
    assert 450 < sequential_ms < 600, f"Sequential took {sequential_ms}ms"
    
    # Parallel should be ~234ms (way faster!)
    assert parallel_ms < sequential_ms * 0.6, \
        f"Parallel ({parallel_ms}ms) not faster than sequential ({sequential_ms}ms)"


@pytest.mark.asyncio
async def test_10_concurrent_invoices():
    """
    Test that 10 concurrent invoices don't block each other.
    
    With proper asyncio, all 10 should process in ~same time
    as 1 invoice.
    """
    
    async def process_invoice(request_id):
        """Simulate invoice processing (tool validation time)."""
        await asyncio.sleep(0.234)  # Single tool
        return f"processed-{request_id}"
    
    # Sequential: 10 × 234ms = 2340ms
    start = time.time()
    for i in range(10):
        await process_invoice(i)
    sequential_ms = (time.time() - start) * 1000
    
    # Concurrent: ~234ms (all run at same time)
    start = time.time()
    await asyncio.gather(*[process_invoice(i) for i in range(10)])
    concurrent_ms = (time.time() - start) * 1000
    
    print(f"Sequential (10 invoices): {sequential_ms:.0f}ms")
    print(f"Concurrent (10 invoices): {concurrent_ms:.0f}ms")
    
    # Concurrent should be WAY faster
    assert concurrent_ms < sequential_ms * 0.15, \
        f"Concurrent not properly parallelized: {concurrent_ms}ms vs {sequential_ms}ms"


@pytest.mark.asyncio
async def test_five_real_http_clients_process_in_parallel(client):
    """
    Spawn real HTTP clients. The thing under test is the agent loop,
    not a mocked sleep.
    """
    AUTH = {"Authorization": "Bearer test-key-12345"}

    async def submit(index: int):
        return await client.post(
            "/process-request",
            headers=AUTH,
            json={
                "request_id": f"INV-PAR-{index:02d}",
                "vendor_id": 1,
                "vendor_name": "Acme Corp Supplies",
                "unit_id": 1,
                "amount": 250.00 + index,
                "date": "2024-09-13",
            },
        )

    start = time.time()
    responses = await asyncio.gather(*[submit(index) for index in range(5)])
    elapsed_ms = (time.time() - start) * 1000
    assert all(item.status_code == 200 for item in responses)
    assert {item.json()["decision"] for item in responses} == {"approved"}
    assert len({item.json()["execution_id"] for item in responses}) == 5
    print(f"Five live invoices: {elapsed_ms:.0f}ms")


def test_asyncio_gather_pattern():
    """
    Test the asyncio.gather() pattern used in agent loop.
    
    This is what WorkCore uses to handle latency.
    """
    
    async def test():
        # This is the pattern from src/agent.py:
        # await asyncio.gather(
        #     validate_vendor(...),
        #     lookup_unit(...),
        #     detect_open_work_orders(...)
        # )
        
        async def mock_tool(name, delay):
            await asyncio.sleep(delay)
            return {name: "result"}
        
        # All tools run at same time
        results = await asyncio.gather(
            mock_tool("validate_vendor", 0.234),
            mock_tool("lookup_unit", 0.156),
            mock_tool("detect_open_work_orders", 0.089)
        )
        
        assert len(results) == 3
        assert results[0] == {"validate_vendor": "result"}
        return True
    
    assert asyncio.run(test())
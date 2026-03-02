import asyncio
import httpx
import uuid
import time

BASE_URL = "http://localhost:8000/api/v1"
USER_ID = uuid.UUID("3d760d0f-c1a1-4698-a078-699dd72dca26")

CONCURRENT_REQUESTS = 50
DEBIT_AMOUNT = 10.0
INITIAL_BALANCE = 100.0
REQUEST_TIMEOUT = 90.0
CONNECTION_LIMIT = 100
MAX_CONCURRENT_DB_CALLS = 10

async def debit_wallet(client: httpx.AsyncClient, request_id: int, amount: float, semaphore: asyncio.Semaphore) -> dict:
    """Single debit request with semaphore to limit DB concurrency"""
    start = time.time()
    async with semaphore:
        try:
            response = await client.post(
                f"{BASE_URL}/wallet/debit",
                params={"user_id": str(USER_ID)},
                json={"amount": amount},
                timeout=REQUEST_TIMEOUT
            )
            elapsed = time.time() - start
            return {
                "request_id": request_id,
                "status": response.status_code,
                "response": response.json() if response.content else None,
                "elapsed_ms": round(elapsed * 1000, 2),
                "error": None
            }
        except httpx.ReadTimeout as e:
            return {"request_id": request_id, "status": None, "response": None, "elapsed_ms": round((time.time() - start) * 1000, 2), "error": "ReadTimeout"}
        except httpx.ConnectError as e:
            return {"request_id": request_id, "status": None, "response": None, "elapsed_ms": 0, "error": "ConnectError"}
        except Exception as e:
            return {"request_id": request_id, "status": None, "response": None, "elapsed_ms": round((time.time() - start) * 1000, 2), "error": f"{type(e).__name__}"}

async def run_concurrent_debits(num_requests: int = CONCURRENT_REQUESTS, amount: float = DEBIT_AMOUNT):
    limits = httpx.Limits(max_connections=CONNECTION_LIMIT, max_keepalive_connections=CONNECTION_LIMIT)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DB_CALLS)  
    async with httpx.AsyncClient(limits=limits) as client:
        start_time = time.time()
        tasks = [debit_wallet(client, req_id, amount, semaphore) for req_id in range(num_requests)]
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        success = [r for r in results if r["status"] == 200]
        failed_400 = [r for r in results if r["status"] == 400]
        failed_other = [r for r in results if r["status"] and r["status"] not in [200, 400]]
        exceptions = [r for r in results if r["error"]]
        
        print("\n RESULTS:")
        print(f"Success (200): {len(success)}")
        print(f"Failed - Insufficient Funds (400): {len(failed_400)}")
        print(f"Failed - Other HTTP: {len(failed_other)}")
        print(f"Exceptions/Timeouts: {len(exceptions)}")
        print(f"Total time: {total_time:.2f}s")
        
        if exceptions:
            print(f"\n Sample errors:")
            for err in exceptions[:3]:
                print(f"Request #{err['request_id']}: {err['error']} ({err['elapsed_ms']}ms)")
        
        print(f"\n🔍 Verifying final balance...")
        try:
            balance_resp = await client.get(f"{BASE_URL}/wallet/balance", params={"user_id": str(USER_ID)}, timeout=10.0)
            final_balance = balance_resp.json()["balance"]
            expected_balance = str(INITIAL_BALANCE - (len(success) * amount))
            print(f"Final balance: {final_balance}")
            print(f"Expected balance: {expected_balance}")
            if str(final_balance) == expected_balance:
                print("BALANCE CORRECTNESS VERIFIED!")
            else:
                print(f"BALANCE MISMATCH!")
                return False
        except Exception as e:
            print(f"Failed to verify balance: {type(e).__name__}: {e}")
            return False
        
        print(f"\n Verifying ledger entries...")
        try:
            ledger_resp = await client.get(f"{BASE_URL}/wallet/ledger", params={"user_id": str(USER_ID)}, timeout=10.0)
            ledger = ledger_resp.json()
            debit_entries = [t for t in ledger["transactions"] if t["transaction_type"] == "DEBIT"]
            print(f"Ledger DEBIT entries: {len(debit_entries)}")
            print(f"Expected DEBIT entries: {len(success)}")
            if len(debit_entries) == len(success):
                print("LEDGER CONSISTENCY VERIFIED!")
                return True
            else:
                print(f"LEDGER MISMATCH!")
                return False
        except Exception as e:
            print(f"Failed to verify ledger: {type(e).__name__}: {e}")
            return False

if __name__ == "__main__":
    success = asyncio.run(run_concurrent_debits())
    exit(0 if success else 1)
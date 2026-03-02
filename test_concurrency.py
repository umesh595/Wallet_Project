import asyncio
import httpx
import uuid
import time
import base64
import json
from decimal import Decimal

BASE_URL = "http://localhost:8000/api/v1"
TEST_USERNAME = "umesh5"
TEST_PASSWORD = "SecurePass123!"
USER_ID = None
ACCESS_TOKEN = None
CONCURRENT_REQUESTS = 50
DEBIT_AMOUNT = 10.0
INITIAL_BALANCE = 100.0
REQUEST_TIMEOUT = 90.0
CONNECTION_LIMIT = 100
MAX_CONCURRENT_DB_CALLS = 10

async def login(client: httpx.AsyncClient, username: str, password: str):
    """Authenticate and get JWT token + user_id"""
    try:
        await client.post(
            f"{BASE_URL}/users",
            params={"password": password},
            json={"username": username, "email": f"{username}@test.com", "full_name": "Test User"},
            timeout=10.0
        )
    except Exception:
        pass  
    form_data = {"username": username, "password": password}
    response = await client.post(
    f"{BASE_URL}/auth/login",
    json={"username": username, "password": password},
    timeout=10.0
)
    if response.status_code != 200:
        raise Exception(f"Login failed: {response.status_code} - {response.text}")
    token_data = response.json()
    access_token = token_data["access_token"]
    payload_b64 = access_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    user_id = uuid.UUID(payload["sub"])
    return access_token, user_id

async def debit_wallet(client: httpx.AsyncClient, request_id: int, amount: float, semaphore: asyncio.Semaphore, token: str) -> dict:
    """Single debit request with JWT auth (unchanged concurrency logic)"""
    start = time.time()
    async with semaphore: 
        try:
            response = await client.post(
                f"{BASE_URL}/wallet/debit",
                json={"amount": amount},  
                headers={"Authorization": f"Bearer {token}"},  
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
        except httpx.ReadTimeout:
            return {"request_id": request_id, "status": None, "response": None, "elapsed_ms": round((time.time() - start) * 1000, 2), "error": "ReadTimeout"}
        except httpx.ConnectError:
            return {"request_id": request_id, "status": None, "response": None, "elapsed_ms": 0, "error": "ConnectError"}
        except Exception as e:
            return {"request_id": request_id, "status": None, "response": None, "elapsed_ms": round((time.time() - start) * 1000, 2), "error": f"{type(e).__name__}"}

async def ensure_wallet_ready(client: httpx.AsyncClient, token: str, target_balance: float):
    """Ensure wallet is ready for test (with JWT auth)"""
    try:
        balance_resp = await client.get(
            f"{BASE_URL}/wallet/balance",
            headers={"Authorization": f"Bearer {token}"},  
            timeout=10.0
        )
        if balance_resp.status_code == 404:
            await client.post(
                f"{BASE_URL}/wallet",
                headers={"Authorization": f"Bearer {token}"}, 
                timeout=10.0
            )
            balance_resp = await client.get(
                f"{BASE_URL}/wallet/balance",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
        current_balance = Decimal(str(balance_resp.json()["balance"]))
        target = Decimal(str(target_balance))
        if current_balance < target:
            amount_needed = float(target - current_balance)
            credit_resp = await client.post(
                f"{BASE_URL}/wallet/credit",
                json={"amount": amount_needed},
                headers={"Authorization": f"Bearer {token}"},  
                timeout=10.0
            )
            if credit_resp.status_code != 200:
                print(f"Failed to credit wallet: {credit_resp.status_code}")
                return False, 0
            print(f"Credited ${amount_needed} to reach target balance ${target_balance}")
        ledger_resp = await client.get(
            f"{BASE_URL}/wallet/ledger",
            headers={"Authorization": f"Bearer {token}"},  
            timeout=10.0
        )
        ledger = ledger_resp.json()
        initial_debit_count = len([t for t in ledger["transactions"] if t["transaction_type"] == "DEBIT"])
        print(f"Initial DEBIT entries in ledger: {initial_debit_count}")
        return True, initial_debit_count
    except Exception as e:
        print(f"Failed to ensure wallet ready: {type(e).__name__}: {e}")
        return False, 0

async def run_concurrent_debits(num_requests: int = CONCURRENT_REQUESTS, amount: float = DEBIT_AMOUNT):
    global ACCESS_TOKEN, USER_ID
    print(f"Starting concurrency test: {num_requests} debits of ${amount}")
    print(f"Expected: {int(INITIAL_BALANCE // amount)} succeed, {num_requests - int(INITIAL_BALANCE // amount)} fail\n")
    limits = httpx.Limits(max_connections=CONNECTION_LIMIT, max_keepalive_connections=CONNECTION_LIMIT)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DB_CALLS)  
    async with httpx.AsyncClient(limits=limits) as client:
        print(f"Authenticating user '{TEST_USERNAME}'...")
        ACCESS_TOKEN, USER_ID = await login(client, TEST_USERNAME, TEST_PASSWORD)
        print(f"Authenticated: user_id={USER_ID}\n")
        print(f"Ensuring wallet is ready with ${INITIAL_BALANCE} balance...")
        ready, initial_debit_count = await ensure_wallet_ready(client, ACCESS_TOKEN, INITIAL_BALANCE)
        if not ready:
            print("Failed to prepare wallet — aborting test")
            return False
        print(f"Firing {num_requests} concurrent debit requests...\n")
        start_time = time.time()
        tasks = [debit_wallet(client, req_id, amount, semaphore, ACCESS_TOKEN) for req_id in range(num_requests)]
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        success = [r for r in results if r["status"] == 200]
        failed_400 = [r for r in results if r["status"] == 400]
        failed_other = [r for r in results if r["status"] and r["status"] not in [200, 400]]
        exceptions = [r for r in results if r["error"]]
        print("RESULTS:")
        print(f"Success (200): {len(success)}")
        print(f"Failed - Insufficient Funds (400): {len(failed_400)}")
        print(f"Failed - Other HTTP: {len(failed_other)}")
        print(f"Exceptions/Timeouts: {len(exceptions)}")
        print(f"Total time: {total_time:.2f}s\n")
        if exceptions:
            print("Sample errors:")
            for err in exceptions[:3]:
                print(f"   Request #{err['request_id']}: {err['error']} ({err['elapsed_ms']}ms)")
            print()
        print("Verifying final balance...")
        try:
            balance_resp = await client.get(
                f"{BASE_URL}/wallet/balance",
                headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}, 
                timeout=10.0
            )
            final_balance = Decimal(str(balance_resp.json()["balance"]))
            expected_balance = Decimal(str(INITIAL_BALANCE)) - (Decimal(str(amount)) * len(success))
            print(f"Final balance: {final_balance}")
            print(f"Expected balance: {expected_balance}")
            if final_balance == expected_balance:
                print("BALANCE CORRECTNESS VERIFIED!\n")
            else:
                print(f"BALANCE MISMATCH! This indicates a concurrency bug.\n")
                return False
        except Exception as e:
            print(f"Failed to verify balance: {type(e).__name__}: {e}\n")
            return False
        print("Verifying ledger entries...")
        try:
            ledger_resp = await client.get(
                f"{BASE_URL}/wallet/ledger",
                headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
                timeout=10.0
            )
            ledger = ledger_resp.json()
            all_debit_entries = [t for t in ledger["transactions"] if t["transaction_type"] == "DEBIT"]
            new_debit_entries = len(all_debit_entries) - initial_debit_count
            expected_new_debits = len(success)   
            print(f"Total DEBIT entries in ledger: {len(all_debit_entries)}")
            print(f"NEW DEBIT entries from this test: {new_debit_entries}")
            print(f"Expected NEW DEBIT entries: {expected_new_debits}")
            
            if new_debit_entries == expected_new_debits:
                print("LEDGER CONSISTENCY VERIFIED!")
                print("\nPHASE 3 JWT AUTH + CONCURRENCY TEST PASSED!")
                return True
            else:
                print(f"LEDGER MISMATCH! Expected {expected_new_debits} new DEBIT entries, found {new_debit_entries}")
                return False
        except Exception as e:
            print(f"Failed to verify ledger: {type(e).__name__}: {e}")
            return False


if __name__ == "__main__":
    success = asyncio.run(run_concurrent_debits())
    exit(0 if success else 1)
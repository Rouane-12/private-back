import os
import requests
import time

KKIAPAY_PRIVATE_KEY = os.getenv("KKIAPAY_PRIVATE_KEY")
KKIAPAY_API = "https://api-sandbox.kkiapay.me/api/v1"


def verify_transaction(transaction_id: str) -> dict:
    resp = requests.post(
        f"{KKIAPAY_API}/transactions/status",
        json={"transactionId": transaction_id},
        headers={
            "x-private-key": KKIAPAY_PRIVATE_KEY,
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    print(f"🔍 Kkiapay status_code: {resp.status_code}")
    print(f"🔍 Kkiapay response: {resp.text}")  # ← CRUCIAL
    if resp.status_code != 200:
        raise Exception(f"Kkiapay error: {resp.text}")
    return resp.json() 

def is_transaction_successful(transaction_id: str) -> tuple[bool, float]:
    data = verify_transaction(transaction_id)
    status = data.get("status", "").upper()
    amount = float(data.get("amount", 0))
    return status in ("SUCCESS", "SUCCESSFUL"), amount


def wait_for_success(transaction_id: str, retries=5):
    for _ in range(retries):
        data = verify_transaction(transaction_id)
        status = data.get("status", "").upper()

        print("STATUS:", status)

        if status in ("SUCCESS", "SUCCESSFUL"):
            return True, float(data.get("amount", 0))

        time.sleep(2)  # attendre 2 sec

    return False, 0


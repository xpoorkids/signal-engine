from fastapi import APIRouter, Request, HTTPException
import hmac
import hashlib
import os

router = APIRouter()

# This MUST match the value in Render env vars
N8N_SHARED_SECRET = os.getenv("N8N_SHARED_SECRET", "")

def verify_signature(raw_body: bytes, signature: str) -> bool:
    if not N8N_SHARED_SECRET:
        return False

    expected = hmac.new(
        N8N_SHARED_SECRET.encode(),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)

@router.post("/scan")
async def scan(request: Request):
    signature = request.headers.get("X-N8N-Signature")
    raw_body = await request.body()

    # 🔒 AUTH IS BACK ON
    if not signature or not verify_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()

    # 🔁 TEMP: simple deterministic response
    return {
        "status": "ok",
        "count": 1,
        "candidates": [
            {
                "symbol": "TEST",
                "chain": "sol",
                "reason": "stub_signal",
                "observed_at": body.get("timestamp"),
                "metrics": {
                    "social_velocity": 0,
                    "volume_delta": 0,
                    "liquidity_usd": 0
                }
            }
        ]
    }

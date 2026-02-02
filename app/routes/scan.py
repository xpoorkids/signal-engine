from fastapi import APIRouter, Request, HTTPException
import hmac, hashlib, os
from app.services.scanner import run_scan

router = APIRouter()

N8N_SHARED_SECRET = os.getenv("N8N_SHARED_SECRET", "")

def verify_signature(body: bytes, signature: str) -> bool:
    if not N8N_SHARED_SECRET:
        return False
    computed = hmac.new(
        N8N_SHARED_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)

@router.post("/scan")
async def scan(request: Request):
    signature = request.headers.get("X-N8N-Signature")
    body = await request.body()

    if not signature or not verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Unauthorized")

    candidates = run_scan()

    return {
        "status": "ok",
        "count": len(candidates),
        "candidates": candidates
    }

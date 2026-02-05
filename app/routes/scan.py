from fastapi import APIRouter, Request, HTTPException
import os
from app.services.scan_service import handle_scan_request, ScanRequestError

"""
Scan ingestion endpoint for automation-driven token discovery.

Authentication:
- Expects X-N8N-Signature header containing HMAC SHA-256 of the raw body.
- Uses shared secret from N8N_SHARED_SECRET environment variable.
"""

router = APIRouter()

# This MUST match the value in Render env vars
N8N_SHARED_SECRET = os.getenv("N8N_SHARED_SECRET", "")

@router.post("/scan")
async def scan(request: Request):
    """
    Ingest a scan payload and return a deterministic response shape.

    Request payload (JSON):
    - expected to include a "timestamp" field used for echoing observed_at
    - additional fields are accepted but not validated here

    Authentication:
    - X-N8N-Signature must be present and valid

    Responses:
    - 200 OK with:
      - status: "ok"
      - count: number of candidates in response
      - candidates: list of candidate objects
    - 401 Unauthorized when signature is missing or invalid
    - 400 Bad Request when JSON is malformed
    - 422 Unprocessable Entity when required fields are missing or invalid
    """
    signature = request.headers.get("X-N8N-Signature")
    raw_body = await request.body()
    try:
        return handle_scan_request(raw_body, signature, N8N_SHARED_SECRET)
    except ScanRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

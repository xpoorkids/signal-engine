# app/routes/scan.py
from app.services.scanner import run_scan

@router.post("/scan")
async def scan(...):
    ...
    candidates = run_scan()
    return { "status": "ok", "candidates": candidates }

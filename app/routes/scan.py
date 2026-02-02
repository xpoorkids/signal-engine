from fastapi import APIRouter
from app.services.scanner import run_scan

router = APIRouter()

@router.post("/scan")
def scan():
    results = run_scan()
    return {"candidates": results}

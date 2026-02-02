from fastapi import APIRouter, Request

router = APIRouter()   # 👈 THIS LINE WAS MISSING

@router.post("/scan")
async def scan(request: Request):
    body = await request.json()
    return {
        "status": "ok",
        "debug": "auth bypassed",
        "received": body
    }

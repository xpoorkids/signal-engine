from fastapi import APIRouter
from app.services.packets import build_packet

router = APIRouter()

@router.get("/packet/{symbol}")
def packet(symbol: str):
    return build_packet(symbol)

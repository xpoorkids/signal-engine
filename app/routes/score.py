from fastapi import APIRouter
from app.services.scorer import score_token
from app.services.watch_store import append_watch_event

router = APIRouter()

@router.post("/score")
def score(payload: dict):
    result = score_token(payload)

    # Persist WATCH decisions for daily summary + learning
    if result.get("status") == "WATCH":
        c = result.get("candidate", {}) or {}
        append_watch_event({
            "token": c.get("symbol") or c.get("address") or c.get("mint"),
            "chain": c.get("chain", "sol"),
            "status": "WATCH",
            "score": result.get("score"),
            "reasons": result.get("reasons", []),
            "rug_risk": result.get("rug_risk"),
            "rug_flags": result.get("rug_flags", []),
            "liquidity": c.get("liquidity"),
            "volume_delta": c.get("volume_delta"),
            "social_velocity": c.get("social_velocity"),
        })

    return result

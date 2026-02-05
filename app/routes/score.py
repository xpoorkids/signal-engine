from fastapi import APIRouter
from app.services.scorer import score_token
from app.services.watch_store import append_watch_event

"""
Score route for candidate evaluation.

Request payload:
- JSON object with a "candidate" field (dict) containing token metrics.

Response schema (shape is preserved from scorer):
- status: "PASS" | "WATCH" | "FAIL"
- score: numeric score
- reasons: list of reason strings
- rug_risk: numeric risk score
- rug_flags: list of rug-risk flags
- candidate: original candidate payload
- reason: present for early FAIL cases
"""

router = APIRouter()

@router.post("/score")
def score(payload: dict):
    """
    Score a candidate payload and return a deterministic classification.

    Side effects:
    - When status == "WATCH", appends a watch event to the JSONL log.

    Error conditions:
    - No explicit validation errors raised here; malformed payloads may
      surface as missing fields in the scorer response.
    """
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

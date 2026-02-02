from fastapi import APIRouter
from app.services.scorer import score_token

router = APIRouter()

@router.post("/score")
def score(payload: dict):
    return score_token(payload)

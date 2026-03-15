from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.review_service import review_contract


router = APIRouter()


class ReviewRequest(BaseModel):
    token: str


@router.get("/review/{token}")
async def review_token(token: str):
    try:
        return await review_contract(token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"review_failed:{exc}")


@router.post("/review")
async def review_token_post(payload: ReviewRequest):
    try:
        return await review_contract(payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"review_failed:{exc}")

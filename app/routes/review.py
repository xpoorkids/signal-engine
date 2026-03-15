from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.services.review_service import review_contract, render_review_html


router = APIRouter()


class ReviewRequest(BaseModel):
    token: str


@router.get("/review/{token}")
async def review_token(token: str, format: str = "html"):
    try:
        review = await review_contract(token)
        if str(format).lower() == "json":
            return review
        return HTMLResponse(content=render_review_html(review))
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

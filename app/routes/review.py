from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.services.review_service import review_contract, render_review_html


router = APIRouter()


class ReviewRequest(BaseModel):
    token: str


class BatchReviewRequest(BaseModel):
    tokens: list[str]


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


@router.post("/review/batch")
async def review_tokens_batch(payload: BatchReviewRequest):
    tokens = [str(token or "").strip() for token in payload.tokens if str(token or "").strip()]
    if not tokens:
        raise HTTPException(status_code=400, detail="tokens_required")
    if len(tokens) > 20:
        raise HTTPException(status_code=400, detail="max_20_tokens")

    results = []
    for token in tokens:
        try:
            results.append(await review_contract(token))
        except ValueError as exc:
            results.append({"token": token, "error": str(exc), "manual_buy_assessment": {"action": "INVALID_CA"}})
        except Exception as exc:
            results.append({"token": token, "error": f"review_failed:{exc}", "manual_buy_assessment": {"action": "ERROR"}})
    return {"count": len(results), "results": results}

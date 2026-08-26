import asyncio

import pytest
from fastapi import HTTPException

from app.routes import review as review_route


def test_review_batch_limits_payload_size(monkeypatch):
    payload = review_route.BatchReviewRequest(tokens=[f"token{i}" for i in range(21)])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(review_route.review_tokens_batch(payload))

    assert exc.value.status_code == 400
    assert exc.value.detail == "max_20_tokens"


def test_review_batch_returns_per_token_errors(monkeypatch):
    async def fake_review_contract(token):
        if token == "bad":
            raise ValueError("invalid_solana_contract_address")
        return {"token": token, "manual_buy_assessment": {"action": "OBSERVE"}}

    monkeypatch.setattr(review_route, "review_contract", fake_review_contract)
    payload = review_route.BatchReviewRequest(tokens=["good", "bad"])

    result = asyncio.run(review_route.review_tokens_batch(payload))

    assert result["count"] == 2
    assert result["results"][0]["manual_buy_assessment"]["action"] == "OBSERVE"
    assert result["results"][1]["manual_buy_assessment"]["action"] == "INVALID_CA"

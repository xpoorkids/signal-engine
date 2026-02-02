def score_token(token: dict):
    # Hard deterministic rules (V1)
    if token.get("liquidity", 0) < 100000:
        return {"status": "FAIL", "reason": "low_liquidity"}

    return {
        "status": "PASS",
        "score": 72
    }

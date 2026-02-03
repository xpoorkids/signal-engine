def score_token(payload: dict):
    candidate = payload.get("candidate", {})

    # Hard deterministic rules (V1)
    if candidate.get("liquidity", 0) < 100000:
        return {
            "status": "FAIL",
            "reason": "low_liquidity",
            "candidate": candidate
        }

    return {
        "status": "PASS",
        "score": 72,
        "candidate": candidate
    }

def score_token(payload: dict):
    candidate = payload.get("candidate", {})

    score = 0
    reasons = []

    liquidity = candidate.get("liquidity", 0)
    volume_delta = candidate.get("volume_delta", 0)
    social_velocity = candidate.get("social_velocity", 0)

    # --- Liquidity ---
    if liquidity < 100_000:
        return {
            "status": "FAIL",
            "reason": "low_liquidity",
            "score": 0,
            "candidate": candidate
        }

    score += 40
    reasons.append("liquidity_ok")

    if liquidity >= 250_000:
        score += 10
        reasons.append("high_liquidity")

    # --- Volume ---
    if volume_delta >= 1.5:
        score += 25
        reasons.append("volume_spike")

    # --- Social ---
    if social_velocity >= 1.2:
        score += 15
        reasons.append("social_momentum")

    # --- Bonus ---
    if liquidity >= 250_000 and volume_delta >= 1.5:
        score += 10
        reasons.append("clean_setup")

    status = "PASS" if score >= 70 else "FAIL"

    return {
        "status": status,
        "score": score,
        "reasons": reasons,
        "candidate": candidate
    }

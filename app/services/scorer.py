def score_token(payload: dict):
    candidate = payload.get("candidate", {})

    score = 0
    reasons = []

    liquidity = candidate.get("liquidity", 0)
    volume_delta = candidate.get("volume_delta", 0)
    social_velocity = candidate.get("social_velocity", 0)

    rug_risk = 0
    rug_flags = []

# --- Rug risk heuristics ---

# Ultra-low liquidity (existence check)
     if liquidity < 5_000:
          rug_risk += 3
          rug_flags.append("ultra_low_liquidity")

# No liquidity growth
    if candidate.get("liquidity_change_1h", 0) <= 0:
          rug_risk += 2
          rug_flags.append("no_liquidity_growth")

# Holder concentration
         top_holder_pct = candidate.get("top_holder_pct", 0)
    if top_holder_pct >= 25:
         rug_risk += 3
         rug_flags.append("top_holder_concentration")

# Creator supply risk
         creator_hold_pct = candidate.get("creator_hold_pct", 0)
    if creator_hold_pct >= 20:
         rug_risk += 3
         rug_flags.append("creator_supply_risk")

# LP lock
    if candidate.get("lp_locked") is False:
         rug_risk += 2
         rug_flags.append("lp_not_locked")

# Volume without liquidity (wash trading signal)
    if volume_delta >= 1.5 and liquidity < 25_000:
         rug_risk += 2
         rug_flags.append("volume_without_liquidity")

    # --- Liquidity (hard gate) ---
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

# --- Rug risk cap ---
    if rug_risk >= 5:
    return {
        "status": "FAIL",
        "reason": "rug_risk_high",
        "score": score,
        "rug_risk": rug_risk,
        "rug_flags": rug_flags,
        "candidate": candidate
    }

    if rug_risk >= 3:
    # Cannot PASS, force WATCH
        reasons.extend(rug_flags)


    # --- Final decision ---
    if score >= 80:
        status = "PASS"
    elif score >= 60:
        status = "WATCH"
    else:
        status = "FAIL"

    return {
        "status": status,
        "score": score,
        "reasons": reasons,
        "candidate": candidate
    }

from __future__ import annotations
from typing import Any, Dict
from .stages import StageDecision, WatchStage
from .stage_config import SOL_STAGE_THRESHOLDS as T

def classify_watch_stage(signals: Dict[str, Any]) -> StageDecision:
    reasons: list[str] = []
    score = 0

    lp = signals.get("lp_usd")
    vol5 = signals.get("vol_5m")
    tx5 = signals.get("tx_5m")
    h_delta = signals.get("holders_delta_15m")
    top10 = signals.get("top10_pct")
    rug_bad = bool(signals.get("rug_bad", False))

    # Hard kill (non-negotiable)
    if rug_bad:
        return StageDecision(
            stage="early",
            score=-999,
            reasons=["rug / critical risk flag"],
            signals=signals,
        )

    # ---------------------------
    # 1️⃣ Liquidity (Solana reality)
    # ---------------------------
    if isinstance(lp, (int, float)):
        if lp >= 60_000:
            score += 3; reasons.append("LP >= 60k (strong exit depth)")
        elif lp >= 30_000:
            score += 2; reasons.append("LP >= 30k")
        elif lp >= 12_000:
            score += 1; reasons.append("LP >= 12k")
        else:
            score -= 2; reasons.append("LP < 12k (exit risk)")

    # ---------------------------
    # 2️⃣ Volume velocity (5m)
    # ---------------------------
    if isinstance(vol5, (int, float)):
        if vol5 >= 25_000:
            score += 4; reasons.append("Vol5m >= 25k (momentum ignition)")
        elif vol5 >= 10_000:
            score += 3; reasons.append("Vol5m >= 10k")
        elif vol5 >= 3_000:
            score += 1; reasons.append("Vol5m >= 3k")
        else:
            score -= 1; reasons.append("Vol5m weak")

    # ---------------------------
    # 3️⃣ Transaction density
    # ---------------------------
    if isinstance(tx5, (int, float)):
        if tx5 >= 120:
            score += 3; reasons.append("Tx5m >= 120 (broad participation)")
        elif tx5 >= 50:
            score += 2; reasons.append("Tx5m >= 50")
        elif tx5 >= 18:
            score += 1; reasons.append("Tx5m >= 18")
        else:
            score -= 1; reasons.append("Tx5m sparse")

    # ---------------------------
    # 4️⃣ Holder expansion (MOST IMPORTANT)
    # ---------------------------
    if isinstance(h_delta, (int, float)):
        if h_delta >= 120:
            score += 4; reasons.append("Holders +120/15m (distribution expanding)")
        elif h_delta >= 50:
            score += 3; reasons.append("Holders +50/15m")
        elif h_delta >= 20:
            score += 1; reasons.append("Holders +20/15m")
        else:
            score -= 1; reasons.append("Holder growth weak")

    # ---------------------------
    # 5️⃣ Concentration penalty
    # ---------------------------
    if isinstance(top10, (int, float)):
        if top10 >= 65:
            score -= 3; reasons.append("Top10 >= 65% (control risk)")
        elif top10 >= 50:
            score -= 2; reasons.append("Top10 >= 50%")
        elif top10 >= 40:
            score -= 1; reasons.append("Top10 >= 40%")

    # ---------------------------
    # Final stage mapping (Solana tuned)
    # ---------------------------
    if score >= 10:
        stage: WatchStage = "near_pass"
    elif score >= 5:
        stage = "building"
    else:
        stage = "early"

    return StageDecision(stage, score, reasons, signals)

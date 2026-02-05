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

        # --- Solana stage thresholds (config-driven) ---
    lp_min = T["lp_usd"]["min"]
    lp_mid = T["lp_usd"]["mid"]
    lp_high = T["lp_usd"]["high"]

    vol_low = T["vol_5m"]["low"]
    vol_mid = T["vol_5m"]["mid"]
    vol_high = T["vol_5m"]["high"]

    tx_low = T["tx_5m"]["low"]
    tx_mid = T["tx_5m"]["mid"]
    tx_high = T["tx_5m"]["high"]

    h_low = T["holders_delta_15m"]["low"]
    h_mid = T["holders_delta_15m"]["mid"]
    h_high = T["holders_delta_15m"]["high"]

    top10_warn = T["top10_pct"]["warn"]
    top10_bad = T["top10_pct"]["bad"]
    top10_severe = T["top10_pct"]["severe"]

    build_cutoff = T["stage_cutoffs"]["building"]
    pass_cutoff = T["stage_cutoffs"]["near_pass"]


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
       if lp >= lp_high:
              score += 3; reasons.append("LP >= high")
       elif lp >= lp_mid:
              score += 2; reasons.append("LP >= mid")
       elif lp >= lp_min:
              score += 1; reasons.append("LP >= min")
       else:
              score -= 2; reasons.append("LP < min")

    # ---------------------------
    # 2️⃣ Volume velocity (5m)
    # ---------------------------
    if isinstance(vol5, (int, float)):
        if vol5 >= vol_high:
             score += 4; reasons.append("Vol5m >= high")
        elif vol5 >= vol_mid:
             score += 3; reasons.append("Vol5m >= mid")
        elif vol5 >= vol_low:
             score += 1; reasons.append("Vol5m >= low")
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
        if h_delta >= h_high:
             score += 4; reasons.append("Holders >= high")
        elif h_delta >= h_mid:
             score += 3; reasons.append("Holders >= mid")
        elif h_delta >= h_low:
             score += 1; reasons.append("Holders >= low")
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
   if score >= pass_cutoff:
    stage: WatchStage = "near_pass"
        elif score >= build_cutoff:
    stage = "building"
        else:
    stage = "early"

    return StageDecision(stage, score, reasons, signals)

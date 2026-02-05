from __future__ import annotations
from typing import Any, Dict, List, Optional, TypedDict
from .stages import StageDecision, WatchStage
from .stage_config import SOL_STAGE_THRESHOLDS as T

class _EvalRecord(TypedDict):
    tick: int
    score: int
    stage: WatchStage

_HISTORY: Dict[str, List[_EvalRecord]] = {}

def _token_key(signals: Dict[str, Any]) -> Optional[str]:
    for key in ("token", "symbol", "address", "mint"):
        value = signals.get(key)
        if isinstance(value, str) and value:
            return value
    return None

def _record_history(token: str, record: _EvalRecord, max_len: int) -> None:
    history = _HISTORY.get(token)
    if history is None:
        history = []
        _HISTORY[token] = history
    history.append(record)
    if len(history) > max_len:
        del history[:-max_len]

def classify_watch_stage(signals: Dict[str, Any]) -> StageDecision:
    """
    Classify a token into a watch stage based on deterministic, config-driven rules.

    Inputs:
    - signals: mapping of metric names to numeric values and flags

    Outputs:
    - StageDecision containing stage, score, reasons, and the original signals

    Invariants:
    - Uses SOL_STAGE_THRESHOLDS for all thresholds and cutoffs
    - Deterministic and explainable scoring (no randomness)
    - Early exit on critical risk flag
    """
    reasons: list[str] = []
    score = 0

    def apply_tiers(
        value: Any,
        metric_key: str,
        label: str,
        tiers: list[tuple[str, int]],
        below_score: int,
        below_reason: str,
    ) -> None:
        """
        Apply tiered scoring for a metric.

        Chooses the first matching tier (highest threshold first in tiers list).
        If no tier matches, applies the below_score and below_reason.
        """
        nonlocal score
        if not isinstance(value, (int, float)):
            return
        thresholds = T[metric_key]
        for tier_key, tier_score in tiers:
            if value >= thresholds[tier_key]:
                score += tier_score
                reasons.append(f"{label} >= {tier_key}")
                return
        score += below_score
        reasons.append(below_reason)

    def apply_penalty(
        value: Any,
        metric_key: str,
        label: str,
        tiers: list[tuple[str, int]],
    ) -> None:
        """
        Apply a penalty based on threshold tiers for a metric.

        Uses the first matching tier in tiers list and adds its score.
        """
        nonlocal score
        if not isinstance(value, (int, float)):
            return
        thresholds = T[metric_key]
        for tier_key, tier_score in tiers:
            if value >= thresholds[tier_key]:
                score += tier_score
                reasons.append(f"{label} >= {tier_key}")
                return

    # Critical risk flag forces early-stage classification.
    if bool(signals.get("rug_bad", False)):
        return StageDecision(
            stage="early",
            score=-999,
            reasons=["rug / critical risk flag"],
            signals=signals,
        )

    apply_tiers(
        signals.get("lp_usd"),
        "lp_usd",
        "LP",
        [("high", 3), ("mid", 2), ("min", 1)],
        -2,
        "LP < min",
    )
    apply_tiers(
        signals.get("vol_5m"),
        "vol_5m",
        "Vol5m",
        [("high", 4), ("mid", 3), ("low", 1)],
        -1,
        "Vol5m weak",
    )
    apply_tiers(
        signals.get("tx_5m"),
        "tx_5m",
        "Tx5m",
        [("high", 3), ("mid", 2), ("low", 1)],
        -1,
        "Tx5m sparse",
    )
    apply_tiers(
        signals.get("holders_delta_15m"),
        "holders_delta_15m",
        "Holders",
        [("high", 4), ("mid", 3), ("low", 1)],
        -1,
        "Holder growth weak",
    )
    apply_penalty(
        signals.get("top10_pct"),
        "top10_pct",
        "Top10",
        [("severe", -3), ("bad", -2), ("warn", -1)],
    )

    # Stage thresholds are config-driven and derived from the cumulative score.
    if score >= T["stage_cutoffs"]["near_pass"]:
        stage: WatchStage = "near_pass"
    elif score >= T["stage_cutoffs"]["building"]:
        stage = "building"
    else:
        stage = "early"

    confirm_cfg = T["near_pass_confirmation"]
    min_consecutive = int(confirm_cfg["min_consecutive"])
    trend_window = int(confirm_cfg["trend_window"])
    min_slope = float(confirm_cfg["min_slope"])
    cooldown_ticks = int(confirm_cfg["promotion_cooldown_ticks"])
    demote_cutoff = int(confirm_cfg["demote_cutoff"])
    history_size = int(confirm_cfg["history_size"])

    final_stage = stage
    final_reasons = list(reasons)

    token_key = _token_key(signals)
    history = _HISTORY.get(token_key, []) if token_key else []
    tick = (history[-1]["tick"] + 1) if history else 1

    if stage == "near_pass":
        if not token_key:
            final_stage = "building"
            final_reasons.append("near_pass_needs_identity")
        else:
            scores = [h["score"] for h in history] + [score]

            consecutive = 0
            for s in reversed(scores):
                if s >= T["stage_cutoffs"]["near_pass"]:
                    consecutive += 1
                else:
                    break
            consecutive_ok = consecutive >= min_consecutive

            if len(scores) >= trend_window:
                window = scores[-trend_window:]
                slope = (window[-1] - window[0]) / (len(window) - 1)
                slope_ok = slope >= min_slope
            else:
                slope_ok = False

            last_promo_tick: Optional[int] = None
            for h in reversed(history):
                if h["stage"] == "near_pass":
                    last_promo_tick = h["tick"]
                    break
            cooldown_ok = last_promo_tick is None or (tick - last_promo_tick) > cooldown_ticks

            if not consecutive_ok:
                final_stage = "building"
                final_reasons.append("near_pass_consecutive_required")
            if not slope_ok:
                final_stage = "building"
                final_reasons.append("near_pass_trend_required")
            if not cooldown_ok:
                final_stage = "building"
                final_reasons.append("near_pass_cooldown_active")

    elif token_key and history:
        if history[-1]["stage"] == "near_pass" and score >= demote_cutoff:
            final_stage = "near_pass"
            final_reasons.append("near_pass_hysteresis")

    if token_key:
        _record_history(
            token_key,
            {"tick": tick, "score": score, "stage": final_stage},
            history_size,
        )

    return StageDecision(final_stage, score, final_reasons, signals)

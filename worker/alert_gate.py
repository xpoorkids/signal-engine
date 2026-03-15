from __future__ import annotations

from typing import Dict, Any, Tuple, List, Optional
import time

from worker.config import (
    ENABLE_ALERT_GATE,
    RISK_VETO_THRESHOLD,
    CAND_MIN_TOKEN_AGE_SEC,
    CAND_MIN_CURVE_LIQ_USD,
    EARLY_ATTENTION_MIN,
)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _get_thresholds_for_age(age_min: float) -> Dict[str, float]:
    # Adaptive gate tuned for Solana junk markets.
    # Tier 1: <2m (very strict)
    if age_min < 2.0:
        return {
            "min_liq": 20000.0,
            "min_vol5m": 12000.0,
            "min_buys5m": 20.0,
            "max_sell_ratio5m": 1.3,
            "max_vol_liq_ratio5m": 4.0,
            "max_price_drop5m": -8.0,
        }
    # Tier 2: 2m - 10m (moderately strict)
    if age_min < 10.0:
        return {
            "min_liq": 12000.0,
            "min_vol5m": 7000.0,
            "min_buys5m": 12.0,
            "max_sell_ratio5m": 1.6,
            "max_vol_liq_ratio5m": 6.0,
            "max_price_drop5m": -12.0,
        }
    # Tier 3: >=10m (more permissive)
    return {
        "min_liq": 8000.0,
        "min_vol5m": 5000.0,
        "min_buys5m": 8.0,
        "max_sell_ratio5m": 2.0,
        "max_vol_liq_ratio5m": 8.0,
        "max_price_drop5m": -18.0,
    }


def evaluate_alert_gate(
    stage: str,
    dex_summary: Optional[Dict[str, Any]],
) -> Tuple[bool, List[str]]:
    if not ENABLE_ALERT_GATE:
        return True, []

    if not dex_summary:
        return True, []

    age_min = _float_or_zero(dex_summary.get("age_minutes"))
    if age_min <= 0:
        return False, ["age_missing"]
    thresholds = _get_thresholds_for_age(age_min)
    reasons: List[str] = []

    liq = _float_or_zero(dex_summary.get("liquidity_usd"))
    vol5m = _float_or_zero(dex_summary.get("volume_m5"))
    buys5m = _int_or_zero(dex_summary.get("txns_m5_buys"))
    sells5m = _int_or_zero(dex_summary.get("txns_m5_sells"))
    chg5m = _float_or_zero(dex_summary.get("price_change_m5"))

    if liq < thresholds["min_liq"]:
        reasons.append(f"liq<{thresholds['min_liq']}")
    if vol5m < thresholds["min_vol5m"]:
        reasons.append(f"vol5m<{thresholds['min_vol5m']}")
    if buys5m < thresholds["min_buys5m"]:
        reasons.append(f"buys5m<{int(thresholds['min_buys5m'])}")
    if chg5m < thresholds["max_price_drop5m"]:
        reasons.append(f"price_change_5m<{thresholds['max_price_drop5m']}")

    if buys5m > 0:
        sell_ratio = sells5m / buys5m
        if sell_ratio > thresholds["max_sell_ratio5m"]:
            reasons.append(f"sell_ratio>{thresholds['max_sell_ratio5m']}")

    if liq > 0:
        vol_liq_ratio = vol5m / liq
        if vol_liq_ratio > thresholds["max_vol_liq_ratio5m"]:
            reasons.append(f"vol_liq_ratio>{thresholds['max_vol_liq_ratio5m']}")

    return len(reasons) == 0, reasons


def _bonding_curve_status(extra: Dict[str, Any]) -> tuple[bool, bool]:
    if not isinstance(extra, dict):
        return False, False
    if "bonding_curve_present" in extra:
        value = extra.get("bonding_curve_present")
        if isinstance(value, bool):
            return True, value
    for key in ("bonding_curve_liquidity", "bonding_curve_liq", "bonding_curve_usd"):
        if key in extra:
            try:
                return True, float(extra.get(key) or 0) > 0
            except Exception:
                return True, False
    bc = extra.get("bonding_curve")
    if isinstance(bc, dict):
        for key in ("liquidity", "liquidity_usd", "lp", "lp_usd"):
            if key in bc:
                try:
                    return True, float(bc.get(key) or 0) > 0
                except Exception:
                    return True, False
        if "exists" in bc and isinstance(bc.get("exists"), bool):
            return True, bool(bc.get("exists"))
    return False, False


def admission_check_candidate(
    attention_score: float,
    risk_score: float,
    extra: Dict[str, Any],
    dex_summary: Optional[Dict[str, Any]],
    attention_unavailable: bool,
) -> tuple[bool, List[str], str]:
    if not ENABLE_ALERT_GATE:
        return True, [], "unknown"

    reasons: List[str] = []
    metrics = extra.get("metrics") if isinstance(extra, dict) else {}
    age_min = _float_or_zero(metrics.get("age_minutes") if isinstance(metrics, dict) else 0)
    age_sec = age_min * 60.0
    age_bypass_until = _float_or_zero(extra.get("age_bypass_until") if isinstance(extra, dict) else 0)
    if age_sec < CAND_MIN_TOKEN_AGE_SEC:
        if not age_bypass_until or time.time() > age_bypass_until:
            reasons.append(f"age<{CAND_MIN_TOKEN_AGE_SEC}s")

    if not attention_unavailable and attention_score < EARLY_ATTENTION_MIN:
        reasons.append(f"attention<{EARLY_ATTENTION_MIN:.2f}")

    if risk_score >= RISK_VETO_THRESHOLD:
        reasons.append("risk_veto")

    lifecycle = "dex" if dex_summary else "bonding_curve"
    if lifecycle == "dex":
        gate_ok, gate_reasons = evaluate_alert_gate("candidate", dex_summary)
        if not gate_ok:
            reasons.extend(f"dex_gate:{reason}" for reason in gate_reasons)
    if lifecycle == "bonding_curve":
        has_bonding, bonding_ok = _bonding_curve_status(extra)
        if has_bonding and not bonding_ok:
            reasons.append("bonding_curve_missing")
        curve_liq = None
        if isinstance(extra.get("bonding_curve_liquidity"), (int, float, str)):
            curve_liq = _float_or_zero(extra.get("bonding_curve_liquidity"))
        elif isinstance(extra.get("bonding_curve"), dict):
            curve_liq = _float_or_zero(extra["bonding_curve"].get("liquidity_usd"))
        if curve_liq is None:
            pass
        elif curve_liq < CAND_MIN_CURVE_LIQ_USD:
            reasons.append(f"curve_liq<{CAND_MIN_CURVE_LIQ_USD:.0f}")

    return len(reasons) == 0, reasons, lifecycle

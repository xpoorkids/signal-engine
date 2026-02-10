from __future__ import annotations

from typing import Dict, Any, Tuple, List, Optional

from worker.config import ENABLE_ALERT_GATE, GATE_REQUIRE_DEX


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
        if GATE_REQUIRE_DEX:
            return False, ["dex_missing"]
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

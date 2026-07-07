"""
Admission and market-structure gate for candidate and promoted alerts.

Purpose
-------
- Centralizes the market-quality and tradeability checks that decide whether a
  token is eligible to become a `candidate` or `promoted` signal.
- Used by `worker.promote` after scoring/enrichment, but before delivery.
- Separates "interesting attention" from "safe enough to alert."

Runtime data flow
-----------------
Inputs:
- Runtime stage (`candidate` or `promoted` in current usage).
- DEX summary metrics when a live pool exists.
- Candidate extras containing age/bonding-curve metrics.
- Attention score, risk score, and tradeability verification flags.

Transformations:
- `evaluate_alert_gate()` applies age-tiered DEX market-quality thresholds.
- `admission_check_candidate()` layers candidate-specific checks on top:
  - age gate and optional age bypass
  - minimum attention
  - risk veto
  - token tradeability verification
  - DEX gate when a pair exists
  - bonding-curve verification and curve-liquidity checks otherwise

Outputs:
- Boolean pass/fail plus machine-readable rejection reasons.
- Lifecycle classification of `dex` vs `bonding_curve` for candidate routing.

Key logic
---------
- DEX thresholds are adaptive by token age:
  - `<2m` is strictest
  - `2m-10m` is moderate
  - `>=10m` is more permissive
- A missing/zero DEX age fails the DEX gate with `age_missing`.
- Candidate gating is stricter than raw attention:
  - attention alone cannot bypass `risk_veto`, `token_unverified`, or missing
    bonding-curve verification on non-DEX paths.
- Non-DEX candidates are treated as bonding-curve lifecycle tokens and require
  explicit curve verification; fungible metadata alone is not enough.

Failure modes
-------------
- Missing DEX summary:
  - `evaluate_alert_gate()` returns pass when no DEX summary is supplied.
  - For candidates this shifts control to the bonding-curve branch.
- Bad or stale `extra["metrics"]` age:
  - Candidate admission can fail on `age<...` even when the token is otherwise
    active.
- Missing tradeability proof:
  - Candidate admission fails with `token_unverified`.
- Weak bonding-curve evidence:
  - Candidate admission fails with `bonding_curve_unverified`,
    `bonding_curve_missing`, or low `curve_liq`.

Logging and observability
-------------------------
- This module returns reasons rather than logging directly.
- The authoritative logs appear in `worker.promote`, especially:
  - `[candidate-skip]`
  - `[gate-skip]`
  - `[promotion-block]`
- When debugging, inspect the exact reason strings produced here because they
  are propagated into those downstream diagnostics.

Dependencies and config
-----------------------
Important config inputs:
- `ENABLE_ALERT_GATE`
- `RISK_VETO_THRESHOLD`
- `CAND_MIN_TOKEN_AGE_SEC`
- `CAND_MIN_CURVE_LIQ_USD`
- `EARLY_ATTENTION_MIN`

Gotchas
-------
- `evaluate_alert_gate()` returning `True` with no `dex_summary` does not mean
  the token is broadly verified; it only means DEX-specific gating was not
  applicable.
- Candidate lifecycle is inferred as `dex` when `dex_summary` exists and
  `bonding_curve` otherwise. Downstream routing depends on that distinction.
- Reason strings are part of live debugging; changing them has operational
  impact because dashboards/log triage often key off these values.
"""

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
from worker.signal_policy import market_quality_thresholds_for_age, candidate_confirmation_signals


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


def _dex_accumulation_watch(extra: Dict[str, Any], dex_summary: Dict[str, Any]) -> bool:
    metrics = extra.get("metrics") if isinstance(extra, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}

    age_min = _float_or_zero(dex_summary.get("age_minutes"))
    liq = _float_or_zero(dex_summary.get("liquidity_usd"))
    vol5m = _float_or_zero(
        dex_summary.get("volume_m5")
        or dex_summary.get("volume_m5_usd")
        or dex_summary.get("volume_5m")
    )
    buys5m = _int_or_zero(dex_summary.get("txns_m5_buys") or dex_summary.get("buys_5m"))
    sells5m = _int_or_zero(dex_summary.get("txns_m5_sells") or dex_summary.get("sells_5m"))
    chg5m = _float_or_zero(dex_summary.get("price_change_m5") or dex_summary.get("price_change_5m"))
    market_cap = _float_or_zero(
        dex_summary.get("market_cap_usd")
        or dex_summary.get("market_cap")
        or dex_summary.get("fdv")
    )
    repeat_count = _int_or_zero(metrics.get("dex_scan_repeat_count"))
    volume_delta = _float_or_zero(metrics.get("dex_scan_volume_delta_5m"))
    persistent = bool(metrics.get("dex_scan_persistent")) or repeat_count >= 2
    independent_flow = bool(metrics.get("independent_flow_confirmed"))
    sell_ratio = sells5m / max(1, buys5m)

    return (
        age_min >= 10.0
        and persistent
        and liq >= 25_000.0
        and vol5m >= 1_000.0
        and buys5m >= 12
        and sell_ratio <= 1.25
        and chg5m >= -18.0
        and 50_000.0 <= market_cap <= 5_000_000.0
        and (independent_flow or volume_delta > 0.0)
    )


def evaluate_alert_gate(
    stage: str,
    dex_summary: Optional[Dict[str, Any]],
) -> Tuple[bool, List[str]]:
    """
    Apply age-tiered DEX market-structure thresholds for alert stages that
    require a real pool and basic market quality.
    """
    if not ENABLE_ALERT_GATE:
        return True, []

    if not dex_summary:
        return True, []

    age_min = _float_or_zero(dex_summary.get("age_minutes"))
    if age_min <= 0:
        return False, ["age_missing"]
    thresholds = market_quality_thresholds_for_age(age_min).as_dict()
    reasons: List[str] = []

    liq = _float_or_zero(dex_summary.get("liquidity_usd"))
    vol5m = _float_or_zero(
        dex_summary.get("volume_m5")
        or dex_summary.get("volume_m5_usd")
        or dex_summary.get("volume_5m")
    )
    buys5m = _int_or_zero(dex_summary.get("txns_m5_buys") or dex_summary.get("buys_5m"))
    sells5m = _int_or_zero(dex_summary.get("txns_m5_sells") or dex_summary.get("sells_5m"))
    chg5m = _float_or_zero(dex_summary.get("price_change_m5") or dex_summary.get("price_change_5m"))

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
    gate_config: Optional[Dict[str, Any]] = None,
    token_is_tradeable: bool = True,
    bonding_curve_verified: bool = False,
) -> tuple[bool, List[str], str]:
    """
    Decide whether a token is eligible to emit a watchlist/candidate signal and
    identify whether it is on the DEX or bonding-curve lifecycle path.
    """
    if not ENABLE_ALERT_GATE:
        return True, [], "unknown"

    reasons: List[str] = []
    config = gate_config if isinstance(gate_config, dict) else {}
    metrics = extra.get("metrics") if isinstance(extra, dict) else {}
    age_min = _float_or_zero(metrics.get("age_minutes") if isinstance(metrics, dict) else 0)
    age_sec = age_min * 60.0
    age_bypass_until = _float_or_zero(extra.get("age_bypass_until") if isinstance(extra, dict) else 0)
    min_age_sec = _int_or_zero(config.get("candidate_gate_min_age_sec")) or CAND_MIN_TOKEN_AGE_SEC
    if age_sec < float(min_age_sec):
        if not age_bypass_until or time.time() > age_bypass_until:
            reasons.append(f"age<{int(min_age_sec)}s")

    min_attention = _float_or_zero(config.get("candidate_gate_attention_min")) or EARLY_ATTENTION_MIN
    if not attention_unavailable and attention_score < min_attention:
        reasons.append(f"attention<{min_attention:.2f}")

    if risk_score >= RISK_VETO_THRESHOLD:
        reasons.append("risk_veto")
    if not token_is_tradeable:
        reasons.append("token_unverified")

    lifecycle = "dex" if dex_summary else "bonding_curve"
    if lifecycle == "dex":
        gate_ok, gate_reasons = evaluate_alert_gate("candidate", dex_summary)
        if not gate_ok:
            reasons.extend(f"dex_gate:{reason}" for reason in gate_reasons)
    if lifecycle == "bonding_curve":
        if not bonding_curve_verified:
            reasons.append("bonding_curve_unverified")
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

    confirmation_reasons, confirmations = candidate_confirmation_signals(
        attention_score=0.0 if attention_score is None else float(attention_score),
        extra=extra,
        dex_summary=dex_summary,
    )
    if confirmation_reasons:
        reasons.extend(confirmation_reasons)
    if confirmations:
        if isinstance(extra, dict):
            extra["candidate_confirmation_signals"] = confirmations
    if lifecycle == "dex" and dex_summary and _dex_accumulation_watch(extra, dex_summary):
        soft_watch_reasons = {
            "dex_gate:vol5m<5000.0",
            "confirmation_signals<2",
        }
        original_reasons = list(reasons)
        reasons = [reason for reason in reasons if reason not in soft_watch_reasons]
        if len(reasons) != len(original_reasons):
            if isinstance(extra, dict):
                extra["candidate_admission_watch_bypass"] = [
                    reason for reason in original_reasons if reason in soft_watch_reasons
                ]
                extra["candidate_confirmation_signals"] = list(
                    dict.fromkeys([*(extra.get("candidate_confirmation_signals") or []), "dex_accumulation_watch"])
                )

    return len(reasons) == 0, reasons, lifecycle

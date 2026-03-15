from __future__ import annotations

import math
from typing import Any


def to_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def to_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def metric_state(
    value: float | int | str | None,
    *,
    status: str,
    reason: str | None = None,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "value": value,
        "status": status,
    }
    if reason:
        payload["reason"] = reason
    if reasons:
        payload["reasons"] = reasons
    return payload


def get_metric_value(container: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(container, dict):
        return None
    metric_states = container.get("metric_states")
    if isinstance(metric_states, dict):
        metric = metric_states.get(key)
        if isinstance(metric, dict) and "value" in metric:
            return metric.get("value")
    return container.get(key)


def get_metric_meta(container: dict[str, Any] | None, key: str) -> dict[str, Any]:
    if not isinstance(container, dict):
        return {}
    metric_states = container.get("metric_states")
    if not isinstance(metric_states, dict):
        return {}
    metric = metric_states.get(key)
    if not isinstance(metric, dict):
        return {}
    return metric


def format_metric_number(
    value: Any,
    *,
    decimals: int = 2,
    missing_label: str = "N/A",
) -> str:
    number = to_optional_float(value)
    if number is None:
        return missing_label
    return f"{number:.{decimals}f}"


def metric_label(
    meta: dict[str, Any] | None,
    *,
    missing_label: str = "N/A",
) -> str:
    if not isinstance(meta, dict):
        return missing_label
    status = str(meta.get("status") or "")
    reason = str(meta.get("reason") or "").replace("_", " ").strip()
    if status in ("disabled", "not_computed"):
        return "Not computed"
    if status == "insufficient_data":
        return "Insufficient data"
    if reason:
        return reason.title()
    return missing_label


def compute_confidence_score(
    *,
    attention_score: float | None,
    risk_score: float | None,
    creator_score: float,
    liquidity_factor: float,
) -> tuple[float, dict[str, Any]]:
    attention_component = attention_score if attention_score is not None else 0.0
    risk_component = 1.0 - (risk_score if risk_score is not None else 0.5)
    score = (
        (attention_component * 0.40)
        + (risk_component * 0.30)
        + (creator_score * 0.20)
        + (liquidity_factor * 0.10)
    )
    return clamp_unit(score), {
        "attention_component": attention_component,
        "risk_component": risk_component,
        "creator_component": creator_score,
        "liquidity_component": liquidity_factor,
        "risk_fallback_applied": risk_score is None,
        "attention_fallback_applied": attention_score is None,
    }


def compute_risk_score(
    *,
    wallet_cluster_ratio: float | None,
    liquidity_stable: bool | None,
    top_holder_ratio: float | None,
    bot_trade_cadence: bool | None,
    mint_authority: bool | None,
    freeze_authority: bool | None,
    liq_usd: float | None,
    liq_locked: bool | None,
    liq_drop_spike: bool | None,
    wallet_risk: dict[str, Any] | None,
) -> dict[str, Any]:
    score = 0.0
    reasons: list[str] = []
    inputs_used: list[str] = []

    if mint_authority is True:
        score += 0.35
        reasons.append("mint_authority_active")
        inputs_used.append("mint_authority")
    if freeze_authority is True:
        score += 0.25
        reasons.append("freeze_authority_active")
        inputs_used.append("freeze_authority")

    if liq_usd is not None:
        inputs_used.append("liq_usd")
        if liq_usd < 2000:
            score += 0.20
            reasons.append("liquidity_very_thin")
        elif liq_usd < 5000:
            score += 0.12
            reasons.append("liquidity_thin")
        elif liq_usd < 15000:
            score += 0.06
            reasons.append("liquidity_subscale")

    if liq_locked is False:
        score += 0.12
        reasons.append("lp_unlocked")
        inputs_used.append("liq_locked")

    if liq_drop_spike:
        score += 0.18
        reasons.append("liquidity_drop_spike")
        inputs_used.append("liq_drop_spike")

    if wallet_cluster_ratio is not None:
        inputs_used.append("wallet_cluster_ratio")
        if wallet_cluster_ratio >= 0.60:
            score += 0.22
            reasons.append("wallet_clustering_high")
        elif wallet_cluster_ratio >= 0.40:
            score += 0.10
            reasons.append("wallet_clustering_watch")

    if top_holder_ratio is not None:
        inputs_used.append("top_holder_ratio")
        if top_holder_ratio >= 0.30:
            score += 0.22
            reasons.append("top_holder_ratio_high")
        elif top_holder_ratio >= 0.18:
            score += 0.10
            reasons.append("top_holder_ratio_watch")

    if bot_trade_cadence:
        score += 0.18
        reasons.append("bot_like_trade_cadence")
        inputs_used.append("bot_trade_cadence")

    wallet_top_holder_pct = None
    wallet_risk_level = None
    if isinstance(wallet_risk, dict):
        wallet_top_holder_pct = to_optional_float(wallet_risk.get("top_holder_pct"))
        wallet_risk_level = wallet_risk.get("risk")
        if wallet_top_holder_pct is not None:
            inputs_used.append("wallet_top_holder_pct")
            if wallet_top_holder_pct >= 0.20:
                score += 0.30
                reasons.append("wallet_top_holder_severe")
            elif wallet_top_holder_pct >= 0.12:
                score += 0.18
                reasons.append("wallet_top_holder_high")
            elif wallet_top_holder_pct >= 0.08:
                score += 0.08
                reasons.append("wallet_top_holder_watch")
        if isinstance(wallet_risk_level, str):
            inputs_used.append("wallet_risk_level")
            if wallet_risk_level == "high":
                score += 0.20
                reasons.append("wallet_risk_high")
            elif wallet_risk_level == "warn":
                score += 0.10
                reasons.append("wallet_risk_warn")

    if not inputs_used:
        return {
            "value": None,
            "status": "insufficient_data",
            "reason": "risk_inputs_unavailable",
            "reasons": [],
            "inputs_used": [],
        }

    return {
        "value": clamp_unit(score),
        "status": "computed",
        "reason": None,
        "reasons": reasons,
        "inputs_used": sorted(set(inputs_used)),
    }

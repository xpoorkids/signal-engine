from __future__ import annotations

from typing import Any

from worker.config import (
    CANDIDATE_EV_BASE_UPSIDE_BPS,
    CANDIDATE_EV_MAX_PRICE_IMPACT_PCT,
    CANDIDATE_EV_MAX_ROUND_TRIP_SLIPPAGE_BPS,
    CANDIDATE_EV_MIN_LIQUIDITY_USD,
    CANDIDATE_EV_MIN_NET_BPS,
    CANDIDATE_EV_REQUIRE_APPROVED_TRADE,
    CANDIDATE_EV_RISK_PENALTY_BPS,
    ENABLE_CANDIDATE_EV_GATE,
)


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _quote_value(quote: dict[str, Any], key: str) -> float | None:
    if not isinstance(quote, dict):
        return None
    return _to_float(quote.get(key))


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def evaluate_candidate_ev(
    trade_validation: dict[str, Any] | None,
    *,
    attention_score: float | None,
    risk_score: float | None,
    dex_summary: dict[str, Any] | None,
    watch_override: bool = False,
) -> dict[str, Any]:
    reasons: list[str] = []
    checks: list[dict[str, Any]] = []

    if not ENABLE_CANDIDATE_EV_GATE:
        return {
            "enabled": False,
            "approved": True,
            "net_edge_bps": None,
            "gross_upside_bps": None,
            "cost_bps": None,
            "risk_penalty_bps": None,
            "round_trip_slippage_bps": None,
            "max_price_impact_pct": None,
            "liquidity_usd": None,
            "reasons": ["ev_gate_disabled"],
            "checks": checks,
        }

    if not isinstance(trade_validation, dict):
        return {
            "enabled": True,
            "approved": False,
            "net_edge_bps": None,
            "gross_upside_bps": None,
            "cost_bps": None,
            "risk_penalty_bps": None,
            "round_trip_slippage_bps": None,
            "max_price_impact_pct": None,
            "liquidity_usd": None,
            "reasons": ["trade_validation_missing"],
            "checks": checks,
        }

    dex = dex_summary if isinstance(dex_summary, dict) else {}
    market_data = trade_validation.get("market_data") if isinstance(trade_validation.get("market_data"), dict) else {}
    buy_quote = trade_validation.get("buy_quote") if isinstance(trade_validation.get("buy_quote"), dict) else {}
    sell_quote = trade_validation.get("sell_quote") if isinstance(trade_validation.get("sell_quote"), dict) else {}

    liquidity_usd = _first_float(
        dex.get("liquidity_usd"),
        market_data.get("liquidity_usd"),
        trade_validation.get("liquidity_usd"),
    )
    buy_slippage_bps = _quote_value(buy_quote, "slippage_bps") or 0.0
    sell_slippage_bps = _quote_value(sell_quote, "slippage_bps") or 0.0
    round_trip_slippage_bps = buy_slippage_bps + sell_slippage_bps
    price_impacts = [
        value
        for value in (
            _quote_value(buy_quote, "price_impact_pct"),
            _quote_value(sell_quote, "price_impact_pct"),
        )
        if value is not None
    ]
    max_price_impact_pct = max(price_impacts) if price_impacts else None

    attn = max(0.0, min(1.0, _to_float(attention_score, 0.0) or 0.0))
    risk = max(0.0, min(1.0, _to_float(risk_score, 0.0) or 0.0))
    attention_multiplier = 0.70 + (attn * 0.60)
    gross_upside_bps = CANDIDATE_EV_BASE_UPSIDE_BPS * attention_multiplier
    if watch_override:
        gross_upside_bps = max(gross_upside_bps, CANDIDATE_EV_BASE_UPSIDE_BPS)
    risk_penalty_bps = risk * CANDIDATE_EV_RISK_PENALTY_BPS
    cost_bps = round_trip_slippage_bps
    net_edge_bps = gross_upside_bps - cost_bps - risk_penalty_bps

    def add_check(name: str, passed: bool, value: Any, threshold: Any) -> None:
        checks.append({"name": name, "passed": passed, "value": value, "threshold": threshold})

    trade_approved = bool(trade_validation.get("approved"))
    add_check("trade_validation_approved", trade_approved, trade_approved, True)
    if CANDIDATE_EV_REQUIRE_APPROVED_TRADE and not trade_approved:
        validation_reasons = trade_validation.get("reasons") if isinstance(trade_validation.get("reasons"), list) else []
        if validation_reasons:
            reasons.extend([f"trade_validation:{reason}" for reason in validation_reasons])
        else:
            reasons.append("trade_validation_rejected")

    liquidity_ok = liquidity_usd is not None and liquidity_usd >= CANDIDATE_EV_MIN_LIQUIDITY_USD
    add_check("min_liquidity_usd", liquidity_ok, liquidity_usd, CANDIDATE_EV_MIN_LIQUIDITY_USD)
    if not liquidity_ok:
        reasons.append("liquidity_below_ev_floor")

    slippage_ok = round_trip_slippage_bps <= CANDIDATE_EV_MAX_ROUND_TRIP_SLIPPAGE_BPS
    add_check("round_trip_slippage_bps", slippage_ok, round_trip_slippage_bps, CANDIDATE_EV_MAX_ROUND_TRIP_SLIPPAGE_BPS)
    if not slippage_ok:
        reasons.append("round_trip_slippage_too_high")

    price_impact_ok = max_price_impact_pct is None or max_price_impact_pct <= CANDIDATE_EV_MAX_PRICE_IMPACT_PCT
    add_check("max_price_impact_pct", price_impact_ok, max_price_impact_pct, CANDIDATE_EV_MAX_PRICE_IMPACT_PCT)
    if not price_impact_ok:
        reasons.append("price_impact_too_high")

    net_edge_ok = net_edge_bps >= CANDIDATE_EV_MIN_NET_BPS
    add_check("min_net_edge_bps", net_edge_ok, net_edge_bps, CANDIDATE_EV_MIN_NET_BPS)
    if not net_edge_ok:
        reasons.append("net_edge_below_floor")

    approved = not reasons
    if approved:
        reasons.append("ev_gate_passed")

    return {
        "enabled": True,
        "approved": approved,
        "net_edge_bps": round(net_edge_bps, 4),
        "gross_upside_bps": round(gross_upside_bps, 4),
        "cost_bps": round(cost_bps, 4),
        "risk_penalty_bps": round(risk_penalty_bps, 4),
        "round_trip_slippage_bps": round(round_trip_slippage_bps, 4),
        "max_price_impact_pct": max_price_impact_pct,
        "liquidity_usd": liquidity_usd,
        "reasons": reasons,
        "checks": checks,
    }

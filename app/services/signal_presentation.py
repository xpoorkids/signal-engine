from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.signal_metrics import get_metric_meta, get_metric_value, metric_label, to_optional_float


@dataclass
class SignalViewModel:
    token: str
    symbol: str
    name: str
    lifecycle: str
    attention_score: float | None
    risk_score: float | None
    confidence_score: float | None
    elite_score: int | None
    market: dict[str, Any] = field(default_factory=dict)
    social: dict[str, Any] = field(default_factory=dict)
    links: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricIntel:
    key: str
    label: str
    value: Any
    status: str
    freshness: str
    source: str
    display: str


def format_currency_compact(value: float | int | None) -> str:
    number = to_optional_float(value)
    if number is None:
        return "N/A"
    abs_num = abs(number)
    if abs_num >= 1_000_000_000:
        return f"${number / 1_000_000_000:.1f}B"
    if abs_num >= 1_000_000:
        return f"${number / 1_000_000:.1f}M"
    if abs_num >= 1_000:
        return f"${number / 1_000:.1f}K"
    return f"${number:,.0f}"


def format_percent_compact(value: float | int | None, *, decimals: int = 0) -> str:
    number = to_optional_float(value)
    if number is None:
        return "N/A"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.{decimals}f}%"


def shorten_address(address: str | None, *, head: int = 6, tail: int = 6) -> str:
    if not address:
        return "unknown"
    if len(address) <= head + tail + 3:
        return address
    return f"{address[:head]}...{address[-tail:]}"


def confidence_band(confidence: float | None) -> str:
    if confidence is None:
        return "Unavailable"
    if confidence >= 0.80:
        return "High conviction"
    if confidence >= 0.65:
        return "Strong"
    if confidence >= 0.45:
        return "Moderate"
    return "Low"


def risk_band(risk_score: float | None) -> str:
    if risk_score is None:
        return "Unavailable"
    if risk_score < 0.20:
        return "Low"
    if risk_score < 0.45:
        return "Mixed"
    if risk_score < 0.70:
        return "Elevated"
    return "High"


def score_band(score: float | None, *, invert: bool = False) -> str:
    if score is None:
        return "Unavailable"
    value = 1.0 - score if invert else score
    if value >= 0.80:
        return "High"
    if value >= 0.60:
        return "Constructive"
    if value >= 0.40:
        return "Mixed"
    return "Weak"


def quality_tier(attention_score: float | None, risk_score: float | None, elite_score: int | None) -> str:
    if elite_score is None or attention_score is None:
        return "Experimental"
    if elite_score >= 10 and attention_score >= 0.75 and (risk_score is None or risk_score <= 0.30):
        return "Tier A"
    if elite_score >= 7 and attention_score >= 0.55:
        return "Tier B"
    return "Tier C"


def flow_bias_label(buys: int | None, sells: int | None) -> str | None:
    if buys is None and sells is None:
        return None
    buy_count = buys or 0
    sell_count = sells or 0
    total = buy_count + sell_count
    if total <= 0:
        return None
    imbalance = (buy_count - sell_count) / total
    if imbalance >= 0.20:
        return "Buy-side"
    if imbalance <= -0.20:
        return "Sell pressure"
    return "Balanced"


def momentum_label(attention_score: float | None, market: dict[str, Any]) -> str:
    chg5 = to_optional_float(market.get("price_change_m5"))
    vol5 = to_optional_float(market.get("volume_m5"))
    if attention_score is None:
        return "Not computed"
    if attention_score >= 0.80 and chg5 is not None and chg5 >= 20:
        return "Confirming"
    if attention_score >= 0.60 and vol5 is not None and vol5 > 0:
        return "Early"
    if attention_score >= 0.45:
        return "Mixed"
    return "Unconfirmed"


def signal_type(signal_kind: str, attention_score: float | None, risk_score: float | None) -> str:
    if signal_kind == "promoted":
        return "promoted"
    if risk_score is not None and risk_score >= 0.70:
        return "risk_alert"
    if attention_score is not None and attention_score >= 0.80 and (risk_score is None or risk_score <= 0.35):
        return "breakout"
    if attention_score is not None and attention_score >= 0.55:
        return "setup"
    return "watch"


def signal_title(signal_class: str, symbol: str) -> str:
    mapping = {
        "promoted": "BREAKOUT",
        "breakout": "BREAKOUT",
        "setup": "SETUP",
        "watch": "WATCH",
        "risk_alert": "RISK ALERT",
    }
    return f"SE // {mapping.get(signal_class, 'WATCH')}  ${symbol}"


def summary_blurb(attention_score: float | None, risk_score: float | None, lifecycle: str) -> str:
    if attention_score is None:
        return "Watch-only setup: core signal quality is still incomplete."
    if lifecycle == "dex" and attention_score >= 0.80 and risk_score is not None and risk_score <= 0.20:
        return "Early breakout confirmation with buy-side flow advantage."
    if attention_score >= 0.70 and (risk_score is None or risk_score < 0.45):
        return "Constructive setup with moderate risk and growing social momentum."
    if risk_score is not None and risk_score >= 0.50:
        return "Watch-only setup: attention present, but continuation still unconfirmed."
    return "Constructive setup developing, but continuation still needs confirmation."


def signal_color(signal_class: str, risk_score: float | None) -> str:
    if signal_class == "risk_alert" or (risk_score is not None and risk_score >= 0.70):
        return "#d74d4d"
    if signal_class in ("breakout", "promoted"):
        return "#2ecc71"
    if signal_class == "setup":
        return "#f4c430"
    return "#2f6bff"


def infer_metric_source(key: str, payload: dict[str, Any] | None) -> str:
    extra = payload if isinstance(payload, dict) else {}
    dex_summary = extra.get("dex_summary") if isinstance(extra.get("dex_summary"), dict) else {}
    attention_metrics = extra.get("attention_metrics") if isinstance(extra.get("attention_metrics"), dict) else {}
    risk_flags = extra.get("risk_flags") if isinstance(extra.get("risk_flags"), dict) else {}

    if key in {"attention_score", "confidence"}:
        return "signal_engine"
    if key in {"risk_score"}:
        return "forensics"
    if key in {"elite_score"}:
        return "elite_model"
    if key == "lifecycle":
        return "signal_engine"
    if key in {"market_cap", "liquidity_usd", "volume_m5", "volume_h1", "txns_m5_buys", "txns_m5_sells", "price_change_m5", "price_change_h1", "price_change_h24"}:
        return "dexscreener" if dex_summary else "unavailable"
    if key in {"x_tweet_count", "x_unique_authors", "x_likes"}:
        return "x_signal" if attention_metrics else "unavailable"
    if key in {"tracked_wallet_hits", "kol_wallet_hits", "narrative_hits", "unique_buyers_5m", "unique_buyers_15m", "burst_count_60s", "dexscreener_boosts_count"}:
        return "attention_engine" if attention_metrics else "unavailable"
    if key in {"wallet_cluster", "holder_concentration", "bot_cadence"}:
        return "forensics" if risk_flags else "unavailable"
    return "signal_engine"


def metric_freshness(meta: dict[str, Any] | None) -> str:
    payload = meta if isinstance(meta, dict) else {}
    status = str(payload.get("status") or "")
    reason = str(payload.get("reason") or "").lower()
    if status in {"disabled", "not_computed"}:
        return "unavailable"
    if status == "insufficient_data":
        return "missing"
    if "stale" in reason or "delayed" in reason:
        return "stale"
    if "inferred" in reason or "fallback" in reason:
        return "inferred"
    return "fresh"


def metric_intel(payload: dict[str, Any] | None, key: str, label: str) -> MetricIntel:
    value = get_metric_value(payload, key)
    meta = get_metric_meta(payload, key)
    status = str(meta.get("status") or ("computed" if value is not None else "unknown"))
    freshness = metric_freshness(meta)
    source = infer_metric_source(key, payload)
    display = metric_label(meta) if value is None else str(value)
    return MetricIntel(
        key=key,
        label=label,
        value=value,
        status=status,
        freshness=freshness,
        source=source,
        display=display,
    )


def build_alert_explanation(
    *,
    signal_kind: str,
    lifecycle: str,
    attention_score: float | None,
    risk_score: float | None,
    confidence_score: float | None,
    payload: dict[str, Any] | None,
    reasons: list[str] | None = None,
) -> dict[str, list[str] | str]:
    extra = payload if isinstance(payload, dict) else {}
    attn = metric_intel(extra, "attention_score", "attention")
    risk = metric_intel(extra, "risk_score", "risk")
    confidence = metric_intel(extra, "confidence", "confidence")
    elite = metric_intel(extra, "elite_score", "elite")
    generic_reasons = {
        "balance_increase_detected",
        "token_resolved",
        "dex_pair_found",
        "sniper_route",
        "promotion_gate_passed",
    }

    why_now: list[str] = []
    if attention_score is not None and attention_score >= 0.80:
        why_now.append("strong attention")
    elif attention_score is not None and attention_score >= 0.55:
        why_now.append("constructive attention")
    elif attn.freshness != "fresh":
        why_now.append(f"attention {attn.display.lower()}")

    if lifecycle == "dex":
        why_now.append("dex live")
    if confidence_score is not None and confidence_score >= 0.65:
        why_now.append("actionable confidence")
    elif confidence.freshness != "fresh":
        why_now.append(f"confidence {confidence.display.lower()}")

    why_not_promoted: list[str] = []
    if risk_score is not None and risk_score >= 0.70:
        why_not_promoted.append("risk too high")
    elif risk_score is not None and risk_score >= 0.45:
        why_not_promoted.append("risk still elevated")
    elif risk.freshness != "fresh":
        why_not_promoted.append(f"risk {risk.display.lower()}")
    if confidence_score is not None and confidence_score < 0.65:
        why_not_promoted.append("confidence below strong band")
    if elite.value is not None and to_optional_float(elite.value) is not None and float(elite.value) < 10:
        why_not_promoted.append("elite below top tier")

    next_steps: list[str] = []
    if attention_score is not None and attention_score < 0.85:
        next_steps.append("stronger breadth + repeated flow")
    if risk_score is not None and risk_score >= 0.45:
        next_steps.append("risk compression")
    if lifecycle != "dex":
        next_steps.append("dex liquidity + pair discovery")
    if confidence_score is not None and confidence_score < 0.65:
        next_steps.append("lift confidence into strong band")
    if not next_steps:
        next_steps.append("monitor continuation")

    data_quality: list[str] = []
    for intel in (attn, risk, confidence, elite):
        if intel.freshness != "fresh":
            data_quality.append(f"{intel.label}: {intel.display.lower()} ({intel.source})")
    if not data_quality:
        data_quality.append(
            "attention / risk / confidence are fresh"
        )

    short_reasons: list[str] = []
    for reason in reasons or []:
        raw = str(reason or "").strip()
        if not raw or raw in generic_reasons:
            continue
        text = raw.replace("_", " ").strip()
        if text and text not in short_reasons:
            short_reasons.append(text)
    if short_reasons and len(why_now) < 3:
        why_now.append(short_reasons[0])

    return {
        "why_now": why_now[:3],
        "why_not_promoted": why_not_promoted[:3],
        "next_steps": next_steps[:3],
        "data_quality": data_quality[:3],
        "profile": signal_type(signal_kind, attention_score, risk_score),
    }

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.signal_metrics import to_optional_float


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

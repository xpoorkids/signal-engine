import json
import requests
import os
from app.services.signal_presentation import SignalViewModel
from app.services.signal_metrics import (
    format_metric_number,
    get_metric_meta,
    get_metric_value,
    metric_label,
    to_optional_float,
)
from worker.config import (
    ENABLE_DISCORD,
    DISCORD_WEBHOOK_URL,
    DISCORD_CANDIDATE_WEBHOOK,
    DRY_RUN,
)
from worker.events import Event
from worker.metadata import fetch_token_metadata
from worker.config import RADAR_QUIET_RISK_MAX


AMBER = 0xF4C430
DARK_RED = 0xC0392B
SLATE = 0x203040


def _full_addr(addr: str | None) -> str:
    if not addr:
        return "unknown"
    return addr


def _fmt_num(value: float | int | None, decimals: int = 0) -> str:
    if value is None:
        return "—"
    try:
        num = float(value)
    except Exception:
        return "—"
    if decimals <= 0:
        return f"{num:,.0f}"
    return f"{num:,.{decimals}f}"


def _fmt_usd(value: float | int | None) -> str:
    if value is None:
        return "—"
    try:
        num = float(value)
    except Exception:
        return "—"
    return f"${num:,.0f}"


def _fmt_age_minutes(age: float | int | None) -> str:
    if age is None:
        return "—"
    try:
        num = float(age)
    except Exception:
        return "—"
    return f"{num:.1f}m"


def _extract_metrics(e: Event) -> dict:
    extra = e.extra if isinstance(e.extra, dict) else {}
    dex_summary = extra.get("dex_summary") if isinstance(extra.get("dex_summary"), dict) else {}
    metrics = extra.get("metrics") if isinstance(extra.get("metrics"), dict) else {}
    attention_metrics = extra.get("attention_metrics") if isinstance(extra.get("attention_metrics"), dict) else {}
    risk_flags = extra.get("risk_flags") if isinstance(extra.get("risk_flags"), dict) else {}
    price_points = None
    if isinstance(extra.get("price_points"), list):
        price_points = extra.get("price_points")
    elif isinstance(dex_summary.get("price_points"), list):
        price_points = dex_summary.get("price_points")

    liq = dex_summary.get("liquidity_usd")
    age = dex_summary.get("age_minutes")
    if liq is None:
        liq = metrics.get("liquidity")
    if age is None:
        age = metrics.get("age_minutes")

    lifecycle = extra.get("lifecycle")
    if not isinstance(lifecycle, str) or not lifecycle:
        lifecycle = "dex" if dex_summary else "bonding_curve"

    return {
        "liq": liq,
        "age": age,
        "unique_buyers_5m": attention_metrics.get("unique_buyers_5m"),
        "unique_buyers_15m": attention_metrics.get("unique_buyers_15m"),
        "volume_m5": dex_summary.get("volume_m5"),
        "volume_h1": dex_summary.get("volume_h1"),
        "txns_m5_buys": dex_summary.get("txns_m5_buys"),
        "txns_m5_sells": dex_summary.get("txns_m5_sells"),
        "price_change_m5": dex_summary.get("price_change_m5"),
        "price_change_h1": dex_summary.get("price_change_h1"),
        "price_change_h24": dex_summary.get("price_change_h24"),
        "market_cap": dex_summary.get("market_cap") or dex_summary.get("fdv"),
        "risk_flags": risk_flags,
        "price_points": price_points,
        "lifecycle": lifecycle,
        "website_url": dex_summary.get("website_url"),
        "twitter_url": dex_summary.get("twitter_url"),
        "telegram_url": dex_summary.get("telegram_url"),
    }


def _format_change_pct(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        num = float(value)
    except Exception:
        return None
    sign = "+" if num > 0 else ""
    return f"{sign}{num:.1f}%"


def render_confidence_pct(score: float | None) -> str:
    clamped = to_optional_float(score)
    if clamped is None:
        return "N/A"
    clamped = max(0.0, min(1.0, clamped))
    return f"{int(round(clamped * 100))}%"


def render_sparkline(points: list[float] | None, width: int = 8) -> str:
    if not points:
        return ""
    series = [p for p in points if isinstance(p, (int, float))]
    if len(series) < 2:
        return ""
    if len(series) > width:
        step = max(1, len(series) // width)
        series = [series[i] for i in range(0, len(series), step)][:width]
    low = min(series)
    high = max(series)
    if high == low:
        return "▁" * len(series)
    blocks = "▁▂▃▄▅▆▇█"
    out = []
    for v in series:
        idx = int(round((v - low) / (high - low) * (len(blocks) - 1)))
        out.append(blocks[idx])
    return "".join(out)


def _section_lines(lines: list[str], indent: int = 2) -> str:
    pad = " " * indent
    return "\n".join(f"{pad}{line}" for line in lines if line)


def _metric_display(extra: dict | None, key: str, *, decimals: int = 2) -> str:
    value = to_optional_float(get_metric_value(extra, key))
    if value is not None:
        return format_metric_number(value, decimals=decimals)
    return metric_label(get_metric_meta(extra, key))


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


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1] + "…"


def _score_band(score: float | None, *, invert: bool = False) -> str:
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


def _summary_blurb(attention_score: float | None, risk_score: float | None, lifecycle: str) -> str:
    if attention_score is None:
        return "Signal quality incomplete. Monitor for fresh market structure."
    if risk_score is not None and risk_score >= 0.70:
        return "Risk is elevated. Treat this as a defensive watch unless structure improves."
    if lifecycle == "dex" and attention_score >= 0.80 and risk_score is not None and risk_score <= 0.20:
        return "Coordination and structure align. Tradable setup with confirmed liquidity."
    if attention_score >= 0.70:
        return "Early coordination detected. Watch for continued buyer breadth and stable liquidity."
    if risk_score is not None and risk_score >= 0.50:
        return "Interest is present, but risk remains elevated. Keep sizing defensive."
    return "Market is forming. Wait for stronger participation or cleaner structure."


def _token_risk_lines(extra: dict | None, lifecycle: str, risk_score: float | None, attention_score: float | None) -> list[str]:
    return [
        _label_value("Lifecycle", _lifecycle_label(lifecycle)),
        _label_value("Attention", _metric_display(extra, "attention_score")),
        _label_value("Risk", _metric_display(extra, "risk_score")),
        _label_value("Conviction", _conviction_label(attention_score, risk_score, lifecycle)),
    ]


def _market_snapshot(metrics: dict) -> list[str]:
    liq = _fmt_usd(metrics.get("liq"))
    mc = _fmt_usd(metrics.get("market_cap"))
    vol5 = _fmt_usd(metrics.get("volume_m5"))
    age = _fmt_age_minutes(metrics.get("age"))
    chg5 = _format_change_pct(metrics.get("price_change_m5")) or "—"
    buys = metrics.get("txns_m5_buys")
    sells = metrics.get("txns_m5_sells")
    flow = "—" if buys is None and sells is None else f"B {buys if buys is not None else 'N/A'} / S {sells if sells is not None else 'N/A'}"
    lines = [
        f"Liquidity: {liq}",
        f"Market Cap: {mc}",
        f"5m Volume: {vol5}",
        f"Age / M5: {age} / {chg5}",
        f"5m Flow: {flow}",
    ]
    spark = render_sparkline(metrics.get("price_points"))
    if spark:
        lines.append(f"Price Path: {spark}")
    return lines


def _flow_bias_label(buys: int | None, sells: int | None) -> str | None:
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


def _momentum_label(attention_score: float | None, metrics: dict) -> str:
    chg5 = to_optional_float(metrics.get("price_change_m5"))
    vol5 = to_optional_float(metrics.get("volume_m5"))
    if attention_score is None:
        return "Not computed"
    if attention_score >= 0.80 and chg5 is not None and chg5 >= 20:
        return "Confirming"
    if attention_score >= 0.60 and vol5 is not None and vol5 > 0:
        return "Early"
    if attention_score >= 0.45:
        return "Mixed"
    return "Unconfirmed"


def _risk_band(risk_score: float | None) -> str:
    if risk_score is None:
        return "Unavailable"
    if risk_score < 0.20:
        return "Low"
    if risk_score < 0.45:
        return "Mixed"
    if risk_score < 0.70:
        return "Elevated"
    return "High"


def _status_dot(color: str) -> str:
    mapping = {
        "green": "🟢",
        "yellow": "🟡",
        "red": "🔴",
        "blue": "🔵",
        "white": "⚪",
    }
    return mapping.get(color, "⚪")


def _risk_dot(risk_score: float | None) -> str:
    if risk_score is None:
        return _status_dot("white")
    if risk_score < 0.20:
        return _status_dot("green")
    if risk_score < 0.45:
        return _status_dot("yellow")
    if risk_score < 0.70:
        return _status_dot("yellow")
    return _status_dot("red")


def _confidence_dot(confidence: float | None) -> str:
    if confidence is None:
        return _status_dot("white")
    if confidence >= 0.80:
        return _status_dot("green")
    if confidence >= 0.45:
        return _status_dot("yellow")
    return _status_dot("red")


def _flow_dot(flow_bias: str | None) -> str:
    if flow_bias == "Buy-side":
        return _status_dot("green")
    if flow_bias == "Balanced":
        return _status_dot("yellow")
    if flow_bias == "Sell pressure":
        return _status_dot("red")
    return _status_dot("white")


def _momentum_dot(label: str) -> str:
    if label == "Confirming":
        return _status_dot("green")
    if label in {"Early", "Mixed"}:
        return _status_dot("yellow")
    if label == "Unconfirmed":
        return _status_dot("red")
    return _status_dot("white")


def _confidence_band(confidence: float | None) -> str:
    if confidence is None:
        return "Unavailable"
    if confidence >= 0.80:
        return "High conviction"
    if confidence >= 0.65:
        return "Strong"
    if confidence >= 0.45:
        return "Moderate"
    return "Low"


def _quality_tier(attention_score: float | None, risk_score: float | None, elite_score: int | None) -> str:
    if elite_score is None or attention_score is None:
        return "Experimental"
    if elite_score >= 10 and attention_score >= 0.75 and (risk_score is None or risk_score <= 0.30):
        return "Tier A"
    if elite_score >= 7 and attention_score >= 0.55:
        return "Tier B"
    return "Tier C"


def _decision_strip(extra: dict | None, confidence_pct: str, lifecycle: str) -> str:
    return " ".join(
        [
            f"`CONF {confidence_pct}`",
            f"`RISK {_metric_display(extra, 'risk_score')}`",
            f"`ATTN {_metric_display(extra, 'attention_score')}`",
            f"`LIFE {lifecycle.upper() if lifecycle else 'N/A'}`",
        ]
    )


def _build_flow_section(metrics: dict, attention_score: float | None, risk_score: float | None) -> str:
    buys = metrics.get("txns_m5_buys")
    sells = metrics.get("txns_m5_sells")
    flow_bias = _flow_bias_label(buys if isinstance(buys, int) else None, sells if isinstance(sells, int) else None)
    momentum = _momentum_label(attention_score, metrics)
    structure = f"Attention {_score_band(attention_score).lower()} / risk {_risk_band(risk_score).lower()}"
    lines = []
    if buys is not None or sells is not None:
        lines.append(f"- {_status_dot('green')} Up 5m Buy Flow: `{buys if buys is not None else 'N/A'}`")
        lines.append(f"- {_status_dot('red')} Down 5m Sell Flow: `{sells if sells is not None else 'N/A'}`")
    if flow_bias:
        lines.append(f"- {_flow_dot(flow_bias)} Flow Bias: `{flow_bias}`")
    lines.append(f"- {_momentum_dot(momentum)} Momentum: `{momentum}`")
    lines.append(f"- Structure: `{structure}`")
    return "\n".join(lines[:5])


def _build_social_section(e: Event) -> str:
    extra = e.extra if isinstance(e.extra, dict) else {}
    attention_metrics = extra.get("attention_metrics") if isinstance(extra.get("attention_metrics"), dict) else {}
    x_mentions = attention_metrics.get("x_tweet_count")
    x_authors = attention_metrics.get("x_unique_authors")
    x_likes = attention_metrics.get("x_likes")
    lines = []
    if x_mentions is not None and x_authors is not None and (x_mentions or x_authors):
        lines.append(f"- X Momentum: `{x_mentions} mentions / {x_authors} authors`")
    if x_likes:
        lines.append(f"- X Engagement: `{x_likes} likes`")
    return "\n".join(lines[:3])


def _build_quality_section(e: Event, metrics: dict, risk_score: float | None, confidence_score: float | None) -> str:
    extra = e.extra if isinstance(e.extra, dict) else {}
    elite = extra.get("elite_score") if isinstance(extra.get("elite_score"), int) else None
    risk_flags = extra.get("risk_flags") if isinstance(extra.get("risk_flags"), dict) else {}
    holder_note = "Concentrated" if risk_flags.get("holder_concentration") else "Organic / unknown"
    lines = [
        f"- {_risk_dot(risk_score)} Risk Score: `{_metric_display(extra, 'risk_score')} ({_risk_band(risk_score)})`",
        f"- {_confidence_dot(confidence_score)} Confidence: `{render_confidence_pct(confidence_score)} ({_confidence_band(confidence_score)})`",
        f"- Elite Score: `{elite if elite is not None else 'N/A'}`",
        f"- Tier: `{_quality_tier(to_optional_float(get_metric_value(extra, 'attention_score')), risk_score, elite)}`",
        f"- Holder Distribution: `{holder_note}`",
    ]
    return "\n".join(lines[:5])


def _market_header_field(metrics: dict) -> dict:
    return {
        "name": "Market Snapshot",
        "value": _market_tape(metrics),
        "inline": False,
    }


def _market_tile_fields(metrics: dict, liq_mc: str) -> list[dict]:
    liq_value = metrics.get("liq")
    liq = format_currency_compact(liq_value)
    mc = format_currency_compact(metrics.get("market_cap"))
    vol5 = format_currency_compact(metrics.get("volume_m5"))
    buys = metrics.get("txns_m5_buys")
    sells = metrics.get("txns_m5_sells")
    flow = "N/A" if buys is None and sells is None else f"B {buys if buys is not None else 'N/A'} / S {sells if sells is not None else 'N/A'}"
    age = _fmt_age_minutes(metrics.get("age"))
    chg5 = _format_change_pct(metrics.get("price_change_m5")) or "N/A"
    liq_name = "LIQ  Unavailable" if liq_value is None else f"LIQ  {liq}"
    return [
        {
            "name": liq_name,
            "value": _section_lines(
                [
                    f"Market Cap: {mc}",
                    f"Liq / MC: {liq_mc}",
                ],
                indent=0,
            ),
            "inline": True,
        },
        {
            "name": f"VOL5  {vol5}",
            "value": _section_lines(
                [
                    f"5m Volume: {vol5}",
                    f"5m Flow: {flow}",
                ],
                indent=0,
            ),
            "inline": True,
        },
        {
            "name": f"AGE  {age}",
            "value": _section_lines(
                [
                    f"Age / M5: {age} / {chg5}",
                ],
                indent=0,
            ),
            "inline": True,
        },
    ]


def get_signal_color(signal_type: str, risk_score: float | None) -> int:
    if signal_type == "risk_alert" or (risk_score is not None and risk_score >= 0.70):
        return DARK_RED
    if signal_type in ("breakout", "promoted"):
        return 0x2ECC71
    if signal_type == "setup":
        return AMBER
    return 0x2F6BFF


def _signal_type(e: Event, attention_score: float | None, risk_score: float | None) -> str:
    if e.type == "promoted":
        return "promoted"
    if risk_score is not None and risk_score >= 0.70:
        return "risk_alert"
    if attention_score is not None and attention_score >= 0.80 and (risk_score is None or risk_score <= 0.35):
        return "breakout"
    if attention_score is not None and attention_score >= 0.55:
        return "setup"
    return "watch"


def _signal_title(signal_type: str, symbol: str) -> str:
    mapping = {
        "promoted": "🔥 SE BREAKOUT",
        "breakout": "🔥 SE BREAKOUT",
        "setup": "🟡 SE SETUP",
        "watch": "🔵 SE WATCH",
        "risk_alert": "🔴 SE RISK ALERT",
    }
    prefix = mapping.get(signal_type, "🔵 SE WATCH")
    return truncate_text(f"{prefix}  ${symbol}", 256)


def _decision_field(extra: dict | None, confidence_pct: str, lifecycle: str, conviction: str, confidence_score: float | None, risk_score: float | None) -> dict:
    return {
        "name": "Command View",
        "value": _section_lines(
            [
                _decision_strip(extra, confidence_pct, lifecycle),
                f"**Conviction:** {conviction}",
                f"**Read:** {_confidence_band(confidence_score)} confidence / {_risk_band(risk_score)} risk",
            ],
            indent=0,
        ),
        "inline": False,
    }


def _trigger_field(e: Event) -> dict | None:
    value = _reason_stack(e)
    if not value or value == "- flow + structure":
        return None
    return {"name": "Signal Intelligence", "value": _section_lines([value]), "inline": False}


def _social_field(e: Event) -> dict | None:
    value = _build_social_section(e)
    if not value:
        return None
    return {"name": "Social", "value": _section_lines([value]), "inline": True}


def _links_field(token: str, metrics: dict) -> dict | None:
    value = _links_lines(token, metrics)
    if not value:
        return None
    return {"name": "Actions", "value": value, "inline": False}


def _trim_field(field: dict) -> dict:
    trimmed = dict(field)
    trimmed["name"] = truncate_text(str(trimmed.get("name") or ""), 256)
    trimmed["value"] = truncate_text(str(trimmed.get("value") or ""), 1024)
    return trimmed


def _finalize_fields(fields: list[dict]) -> list[dict]:
    compact = [_trim_field(field) for field in fields if field and field.get("value")]
    return compact[:25]


def _candidate_header(attention_score: float | None, risk_score: float | None) -> str:
    if attention_score is None:
        return "SE // MONITOR"
    if attention_score >= 0.85:
        regime = "BREAKOUT"
    elif attention_score >= 0.70:
        regime = "SETUP"
    elif risk_score is not None and risk_score < RADAR_QUIET_RISK_MAX:
        regime = "WATCH"
    elif risk_score is not None and risk_score < 0.50:
        regime = "SETUP"
    else:
        regime = "SETUP"
    return f"SE // {regime}"


def _promoted_header(final_score: float) -> str:
    if final_score >= 0.80:
        regime = "STRONG"
    elif final_score >= 0.75:
        regime = "NORMAL"
    else:
        regime = "NORMAL"
    return f"SE // VALIDATED {regime}"


def _conviction_label(attention_score: float | None, risk_score: float | None, lifecycle: str) -> str:
    if attention_score is None:
        return "Not computed"
    if lifecycle == "dex" and attention_score >= 0.85 and risk_score is not None and risk_score <= 0.15:
        return "Momentum confirmed"
    if attention_score >= 0.80:
        return "Strong coordination"
    if attention_score >= 0.65:
        return "Early follow-through"
    return "Developing flow"


def _wallet_signal_lines(risk_flags: dict) -> str:
    if not isinstance(risk_flags, dict):
        return "- organic holder distribution"
    order = [
        ("wallet_cluster", "wallet clustering"),
        ("holder_concentration", "holder concentration"),
        ("bot_cadence", "bot-like cadence"),
    ]
    lines = []
    for key, label in order:
        if risk_flags.get(key):
            lines.append(f"- {label}")
        if len(lines) >= 3:
            break
    if not lines:
        return "- organic holder distribution"
    return "\n".join(lines)


def _attention_signal_lines(e: Event, risk_flags: dict) -> str:
    base = _wallet_signal_lines(risk_flags).splitlines()
    lines = [line for line in base if line]
    extra = e.extra if isinstance(e.extra, dict) else {}
    attention_metrics = extra.get("attention_metrics") if isinstance(extra.get("attention_metrics"), dict) else {}
    tracked_hits = int(attention_metrics.get("tracked_wallet_hits") or 0)
    kol_hits = int(attention_metrics.get("kol_wallet_hits") or 0)
    narrative_hits = attention_metrics.get("narrative_hits") if isinstance(attention_metrics.get("narrative_hits"), list) else []
    x_tweet_count = int(attention_metrics.get("x_tweet_count") or 0)
    x_unique_authors = int(attention_metrics.get("x_unique_authors") or 0)
    if tracked_hits > 0:
        lines.append(f"- smart wallets: {tracked_hits}")
    if kol_hits > 0:
        lines.append(f"- kol wallets: {kol_hits}")
    if narrative_hits:
        lines.append(f"- narrative: {', '.join(str(x) for x in narrative_hits[:2])}")
    if x_tweet_count > 0:
        lines.append(f"- x momentum: {x_tweet_count} mentions / {x_unique_authors} authors")
    return "\n".join(lines[:4]) if lines else "- organic holder distribution"


def _pretty_reason(reason: str) -> str:
    mapping = {
        "dex_pair_found": "Dex pair is live",
        "token_resolved": "Token metadata resolved",
        "sniper_route": "Sniper route triggered",
        "promotion_gate_passed": "Promotion gate passed",
        "wallet_low_risk": "Creator wallet risk is low",
        "dexscreener_boost": "DexScreener boost detected",
        "birdeye_trending": "Birdeye trending",
        "tracked_wallet_flow": "Smart wallet flow detected",
        "kol_wallet_flow": "KOL wallet flow detected",
        "x_social_momentum": "X social momentum detected",
    }
    if reason in mapping:
        return mapping[reason]
    if reason.startswith("repeat_"):
        return f"Repeat signal count: {reason.split('_', 1)[1]}"
    if reason.startswith("narrative:"):
        return f"Narrative alignment: {reason.split(':', 1)[1].replace(',', ', ')}"
    return reason.replace("_", " ")


def _security_lines(e: Event, metrics: dict, risk_score: float | None) -> str:
    extra = e.extra if isinstance(e.extra, dict) else {}
    risk_meta = get_metric_meta(extra, "risk_score")
    if risk_score is None:
        lines = [f"- Risk Score: {metric_label(risk_meta)}"]
    else:
        lines = [f"- Risk Score: {format_metric_number(risk_score, decimals=2)} ({_score_band(risk_score, invert=True)})"]
    elite = extra.get("elite_score")
    if elite is not None:
        elite_band = "Elite" if elite >= 10 else "Strong" if elite >= 8 else "Developing" if elite >= 5 else "Weak"
        lines.append(f"- Elite Score: {elite} ({elite_band})")
    lifecycle = metrics.get("lifecycle")
    if lifecycle:
        lines.append(f"- Lifecycle: {_lifecycle_label(lifecycle)}")
    buys = metrics.get("txns_m5_buys")
    sells = metrics.get("txns_m5_sells")
    if buys is not None or sells is not None:
        lines.append(f"- 5m Flow: B {buys if buys is not None else 'N/A'} / S {sells if sells is not None else 'N/A'}")
    return "\n".join(lines[:5])


def _stats_lines(metrics: dict) -> str:
    lines = []
    age = _fmt_age_minutes(metrics.get("age"))
    if age != "—":
        lines.append(f"- age: {age}")
    vol_m5 = _fmt_usd(metrics.get("volume_m5"))
    if vol_m5 != "—":
        lines.append(f"- vol 5m: {vol_m5}")
    vol_h1 = _fmt_usd(metrics.get("volume_h1"))
    if vol_h1 != "—":
        lines.append(f"- vol 1h: {vol_h1}")
    chg_m5 = _format_change_pct(metrics.get("price_change_m5"))
    if chg_m5:
        lines.append(f"- chg 5m: {chg_m5}")
    chg_h1 = _format_change_pct(metrics.get("price_change_h1"))
    if chg_h1:
        lines.append(f"- chg 1h: {chg_h1}")
    return "\n".join(lines[:4]) if lines else "- early / pending"


def _engine_public_base_url() -> str:
    explicit = os.getenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render_url:
        return render_url
    render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if render_host:
        return f"https://{render_host}".rstrip("/")
    return ""


def _links_lines(token: str, metrics: dict) -> str:
    lines = [
        f"[Dexscreener](https://dexscreener.com/solana/{token}) | [Birdeye](https://birdeye.so/token/{token}?chain=solana)"
    ]
    twitter_url = metrics.get("twitter_url")
    website_url = metrics.get("website_url")
    telegram_url = metrics.get("telegram_url")
    socials = []
    if isinstance(twitter_url, str) and twitter_url:
        socials.append(f"[X]({twitter_url})")
    if isinstance(website_url, str) and website_url:
        socials.append(f"[Web]({website_url})")
    if isinstance(telegram_url, str) and telegram_url:
        socials.append(f"[TG]({telegram_url})")
    if socials:
        lines.append(" | ".join(socials))
    engine_base = _engine_public_base_url()
    if engine_base:
        lines.append(
            " | ".join(
                [
                    f"[Ops]({engine_base}/learning/command-center/dashboard)",
                    f"[Verify]({engine_base}/learning/tuning/verification/dashboard)",
                    f"[Incidents]({engine_base}/learning/tuning/incidents/dashboard)",
                ]
            )
        )
    return _section_lines(lines)


def _reason_stack(e: Event) -> str:
    extra = e.extra if isinstance(e.extra, dict) else {}
    attn_reasons = extra.get("attention_reasons") if isinstance(extra.get("attention_reasons"), list) else []
    reasons = []
    for reason in attn_reasons:
        if isinstance(reason, str):
            reasons.append(reason)
    generic_reasons = {
        "balance_increase_detected",
        "token_resolved",
        "dex_pair_found",
        "sniper_route",
        "promotion_gate_passed",
    }
    if isinstance(e.reasons, list):
        for reason in e.reasons:
            if isinstance(reason, str) and reason not in generic_reasons:
                reasons.append(reason)
    seen = []
    for reason in reasons:
        if reason not in seen and not reason.startswith("source_unavailable"):
            seen.append(reason)
    if not seen:
        return "- flow + structure"
    return "\n".join(f"- {_pretty_reason(reason)}" for reason in seen[:4])


def _market_tape(metrics: dict) -> str:
    liq = _fmt_usd(metrics.get("liq"))
    mc = _fmt_usd(metrics.get("market_cap"))
    vol5 = _fmt_usd(metrics.get("volume_m5"))
    age = _fmt_age_minutes(metrics.get("age"))
    chg5 = _format_change_pct(metrics.get("price_change_m5")) or "—"
    return f"`LIQ {liq}` `MC {mc}` `VOL5 {vol5}` `AGE {age}` `M5 {chg5}`"


def _token_labels_from_event(e: Event) -> tuple[str, str]:
    symbol = ""
    name = ""
    if isinstance(e.extra, dict):
        sym = e.extra.get("symbol")
        if isinstance(sym, str) and sym.strip():
            symbol = sym.strip().upper()
        raw_name = e.extra.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            name = raw_name.strip()
        dex_summary = e.extra.get("dex_summary") if isinstance(e.extra.get("dex_summary"), dict) else {}
        dex_symbol = dex_summary.get("symbol")
        if not symbol and isinstance(dex_symbol, str) and dex_symbol.strip():
            symbol = dex_symbol.strip().upper()
        dex_name = dex_summary.get("name")
        if not name and isinstance(dex_name, str) and dex_name.strip():
            name = dex_name.strip()
    if e.token and (not symbol or not name):
        meta = fetch_token_metadata(e.token)
        if isinstance(meta, dict):
            meta_symbol = meta.get("symbol")
            meta_name = meta.get("name")
            if not symbol and isinstance(meta_symbol, str) and meta_symbol.strip():
                symbol = meta_symbol.strip().upper()
            if not name and isinstance(meta_name, str) and meta_name.strip():
                name = meta_name.strip()
    if not symbol:
        symbol = "UNK"
    if not name:
        name = symbol
    return symbol, name


def _display_confidence_score(e: Event, attention_score: float | None) -> float | None:
    raw = to_optional_float(e.confidence)
    if raw is not None:
        return max(0.0, min(1.0, raw))
    if attention_score is not None:
        return max(0.0, min(1.0, attention_score))
    return None


def _fmt_title(e: Event) -> str:
    if e.type == "promoted":
        return "🔴 Promoted Signal"
    if e.type == "candidate":
        return "🟡 Attention Candidate"
    if e.type == "heating_up":
        return "HEATING UP"
    if e.type.startswith("early"):
        return "EARLY"
    return f"INFO {e.type}"


def _label_value(label: str, value: str, width: int = 14) -> str:
    return f"{label:<{width}}{value}"


def _lifecycle_label(raw: str | None) -> str:
    if not raw:
        return "Unknown"
    if raw == "bonding_curve":
        return "Bonding Curve"
    if raw == "dex":
        return "Dex"
    return raw.replace("_", " ").title()


def _build_overview_lines(
    symbol: str,
    name: str,
    full_addr: str,
) -> list[str]:
    return [
        f"**Token:** {name} (`${symbol}`)",
        f"**Contract:** `{full_addr}`",
    ]


def _build_market_snapshot_lines(
    liq: str,
    mc: str,
    liq_mc: str,
) -> list[str]:
    return [
        f"Liquidity: {liq}",
        f"Market Cap: {mc}",
        f"Liq / MC: {liq_mc}",
    ]


def _summary_field(name: str, value: str) -> dict:
    return {"name": name, "value": value, "inline": True}


def _format_candidate_like(e: Event, description: str) -> dict:
    token = e.token or "unknown"
    symbol, name = _token_labels_from_event(e)
    full_addr = _full_addr(token)
    attention_score = to_optional_float(get_metric_value(e.extra, "attention_score")) if isinstance(e.extra, dict) else None
    risk_score = to_optional_float(get_metric_value(e.extra, "risk_score")) if isinstance(e.extra, dict) else None

    metrics = _extract_metrics(e)
    lifecycle = str(metrics.get("lifecycle") or "")
    confidence_score = _display_confidence_score(e, attention_score)
    confidence_pct = render_confidence_pct(confidence_score)
    mc_value = metrics.get("market_cap")
    liq_value = metrics.get("liq")
    liq_mc = "—"
    try:
        if mc_value and float(mc_value) > 0 and liq_value is not None:
            liq_mc = f"{round((float(liq_value) / float(mc_value)) * 100)}%"
    except Exception:
        liq_mc = "—"

    vm = SignalViewModel(
        token=token,
        symbol=symbol,
        name=name,
        lifecycle=lifecycle,
        attention_score=attention_score,
        risk_score=risk_score,
        confidence_score=confidence_score,
        elite_score=e.extra.get("elite_score") if isinstance(e.extra, dict) and isinstance(e.extra.get("elite_score"), int) else None,
        market=metrics,
    )
    conviction = _conviction_label(vm.attention_score, vm.risk_score, vm.lifecycle)
    signal_type = _signal_type(e, vm.attention_score, vm.risk_score)
    fields = _finalize_fields(
        [
            _decision_field(e.extra if isinstance(e.extra, dict) else {}, confidence_pct, vm.lifecycle, conviction, vm.confidence_score, vm.risk_score),
            {
                "name": "Token Identity",
                "value": _section_lines(
                    _build_overview_lines(
                        symbol,
                        name,
                        full_addr,
                    )
                ),
                "inline": False,
            },
            _market_header_field(metrics),
            *_market_tile_fields(metrics, liq_mc),
            {
                "name": "Flow + Structure",
                "value": _section_lines([_build_flow_section(metrics, vm.attention_score, vm.risk_score)]),
                "inline": True,
            },
            {
                "name": "Quality",
                "value": _section_lines([_build_quality_section(e, metrics, vm.risk_score, vm.confidence_score)]),
                "inline": True,
            },
            _social_field(e),
            _trigger_field(e),
            _links_field(token, metrics),
        ]
    )

    embed = {
        "title": _signal_title(signal_type, vm.symbol),
        "description": f"{_summary_blurb(vm.attention_score, vm.risk_score, vm.lifecycle)}\n",
        "color": get_signal_color(signal_type, vm.risk_score),
        "fields": fields,
        "footer": {"text": truncate_text(f"Signal Engine  Radar Deck  {e.type}", 2048)},
    }
    return {"embeds": [embed]}


def format_discord(e: Event) -> dict:
    token = e.token or "unknown"
    symbol, name = _token_labels_from_event(e)
    full_addr = _full_addr(token)

    if e.type == "promoted":
        reasons = []
        if e.reasons:
            reasons = e.reasons[:4]
        rscore = to_optional_float(get_metric_value(e.extra, "risk_score")) if isinstance(e.extra, dict) else None
        ascore = to_optional_float(get_metric_value(e.extra, "attention_score")) if isinstance(e.extra, dict) else None
        metrics = _extract_metrics(e)
        lifecycle = str(metrics.get("lifecycle") or "")
        confidence_score = _display_confidence_score(e, ascore)
        confidence_pct = render_confidence_pct(confidence_score)
        mc_value = metrics.get("market_cap")
        liq_value = metrics.get("liq")
        liq_mc = "—"
        try:
            if mc_value and float(mc_value) > 0 and liq_value is not None:
                liq_mc = f"{round((float(liq_value) / float(mc_value)) * 100)}%"
        except Exception:
            liq_mc = "—"

        vm = SignalViewModel(
            token=token,
            symbol=symbol,
            name=name,
            lifecycle=lifecycle,
            attention_score=ascore,
            risk_score=rscore,
            confidence_score=confidence_score,
            elite_score=e.extra.get("elite_score") if isinstance(e.extra, dict) and isinstance(e.extra.get("elite_score"), int) else None,
            market=metrics,
        )
        conviction = _conviction_label(vm.attention_score, vm.risk_score, vm.lifecycle)
        signal_type = _signal_type(e, vm.attention_score, vm.risk_score)
        fields = _finalize_fields(
            [
                _decision_field(e.extra if isinstance(e.extra, dict) else {}, confidence_pct, vm.lifecycle, conviction, vm.confidence_score, vm.risk_score),
                {
                    "name": "Token Identity",
                    "value": _section_lines(
                        _build_overview_lines(
                            symbol,
                            name,
                            full_addr,
                        )
                    ),
                    "inline": False,
                },
                _market_header_field(metrics),
                *_market_tile_fields(metrics, liq_mc),
                {
                    "name": "Flow + Structure",
                    "value": _section_lines([_build_flow_section(metrics, vm.attention_score, vm.risk_score)]),
                    "inline": True,
                },
                {
                    "name": "Quality",
                    "value": _section_lines([_build_quality_section(e, metrics, vm.risk_score, vm.confidence_score)]),
                    "inline": True,
                },
                _social_field(e),
                _trigger_field(e),
                {"name": "Why Promoted", "value": _section_lines([f"- {_pretty_reason(r)}" for r in reasons]), "inline": False} if reasons else None,
                _links_field(token, metrics),
            ]
        )

        embed = {
            "title": _signal_title(signal_type, vm.symbol),
            "description": f"{_summary_blurb(vm.attention_score, vm.risk_score, vm.lifecycle)}\n",
            "color": get_signal_color(signal_type, vm.risk_score),
            "fields": fields,
            "footer": {"text": truncate_text(f"Signal Engine  Alpha Deck  {e.type}", 2048)},
        }
        return {"embeds": [embed]}

    if e.type == "candidate":
        return _format_candidate_like(e, "Early coordination detected. Watch only.")
    if e.type == "heating_up":
        return _format_candidate_like(e, "Heating up. Monitor for confirmation.")
    return _format_candidate_like(e, "Radar update.")


def send_discord(e: Event) -> None:
    if not ENABLE_DISCORD:
        return
    if not DISCORD_WEBHOOK_URL:
        print("[discord] missing DISCORD_WEBHOOK_URL")
        return

    payload = format_discord(e)
    print(f"[discord] send attempt type={e.type} token={e.token}", flush=True)

    if DRY_RUN:
        print("[DRY_RUN] suppressed Discord send", json.dumps(payload)[:400])
        return

    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=8)
        print(f"[discord-http] status={r.status_code} token={e.token}", flush=True)
        if r.status_code >= 300:
            print(f"[discord-http-error] status={r.status_code} token={e.token} body={r.text[:200]}", flush=True)
            print("[discord] send failed", r.status_code, r.text[:200])
        else:
            print("[discord] send ok", r.status_code)
    except Exception as ex:
        print(f"[discord-http-error] token={e.token} error={ex}", flush=True)
        print("[discord] send exception", ex)


def send_candidate_discord(e: Event, message_id: str | None = None) -> str | None:
    if not ENABLE_DISCORD:
        return
    if not DISCORD_CANDIDATE_WEBHOOK:
        print("[discord] missing DISCORD_CANDIDATE_WEBHOOK")
        return
    if e.type != "candidate":
        return

    payload = format_discord(e)
    print(f"[discord] candidate send attempt token={e.token}", flush=True)

    if DRY_RUN:
        print("[DRY_RUN] suppressed Candidate Discord send", json.dumps(payload)[:400])
        return None

    try:
        if message_id:
            url = f"{DISCORD_CANDIDATE_WEBHOOK}/messages/{message_id}"
            r = requests.patch(url, json=payload, timeout=8)
        else:
            url = f"{DISCORD_CANDIDATE_WEBHOOK}?wait=true"
            r = requests.post(url, json=payload, timeout=8)
        if r.status_code >= 300:
            print("[discord] candidate send failed", r.status_code, r.text[:200])
        else:
            print("[discord] candidate send ok", r.status_code)
            if not message_id:
                try:
                    data = r.json()
                    return data.get("id")
                except Exception:
                    return None
    except Exception as ex:
        print("[discord] candidate send exception", ex)
    return None

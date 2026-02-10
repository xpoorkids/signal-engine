import json
import requests
from worker.config import (
    ENABLE_DISCORD,
    DISCORD_WEBHOOK_URL,
    DISCORD_CANDIDATE_WEBHOOK,
    DRY_RUN,
)
from worker.events import Event


AMBER = 0xF4C430
DARK_RED = 0xC0392B


def _short_addr(addr: str | None) -> str:
    if not addr:
        return "unknown"
    if len(addr) <= 8:
        return addr
    return f"{addr[:4]}{addr[-4:]}"


def _fmt_num(value: float | int | None, decimals: int = 0) -> str:
    if value is None:
        return "-"
    try:
        num = float(value)
    except Exception:
        return "-"
    if decimals <= 0:
        return f"{num:,.0f}"
    return f"{num:,.{decimals}f}"


def _fmt_usd(value: float | int | None) -> str:
    if value is None:
        return "-"
    try:
        num = float(value)
    except Exception:
        return "-"
    return f"${num:,.0f}"


def _fmt_age_minutes(age: float | int | None) -> str:
    if age is None:
        return "-"
    try:
        num = float(age)
    except Exception:
        return "-"
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

    return {
        "liq": liq,
        "age": age,
        "unique_buyers_5m": attention_metrics.get("unique_buyers_5m"),
        "unique_buyers_15m": attention_metrics.get("unique_buyers_15m"),
        "price_change_h24": dex_summary.get("price_change_h24"),
        "market_cap": dex_summary.get("market_cap") or dex_summary.get("fdv"),
        "risk_flags": risk_flags,
        "price_points": price_points,
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


def render_confidence_bar(score: float) -> str:
    blocks = 5
    try:
        clamped = max(0.0, min(1.0, float(score)))
    except Exception:
        clamped = 0.0
    filled = int(round(clamped * blocks))
    return ("#" * filled) + ("-" * (blocks - filled))


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


def _section_lines(lines: list[str], indent: int = 2, bar: str = "|") -> str:
    pad = " " * indent
    return "\n".join(f"{bar}{pad}{line}" for line in lines if line)


def _candidate_header(attention_score: float, risk_score: float) -> str:
    if attention_score >= 0.85:
        regime = "Radar (Hot)"
    elif attention_score >= 0.70:
        regime = "Radar (Active)"
    elif risk_score < 0.30:
        regime = "Radar (Quiet)"
    else:
        regime = "Radar (Active)"
    return f"[ RADAR   WATCH ] {regime}"


def _promoted_header(final_score: float) -> str:
    if final_score >= 0.80:
        regime = "Signal (Strong)"
    elif final_score >= 0.75:
        regime = "Signal (Normal)"
    else:
        regime = "Signal (Normal)"
    return f"[ SIGNAL   VALIDATED ] {regime}"


def _wallet_signal_lines(risk_flags: dict) -> str:
    if not isinstance(risk_flags, dict):
        return "organic holder distribution"
    order = [
        ("wallet_cluster", "cluster_detected"),
        ("holder_concentration", "holder_concentration"),
        ("bot_cadence", "bot_cadence"),
    ]
    lines = []
    for key, label in order:
        if risk_flags.get(key):
            lines.append(f"- {label}")
        if len(lines) >= 3:
            break
    if not lines:
        return "organic holder distribution"
    return "\n".join(lines)


def _symbol_from_event(e: Event) -> str:
    if isinstance(e.extra, dict):
        sym = e.extra.get("symbol")
        if isinstance(sym, str) and sym.strip():
            return sym.strip().upper()
    return "UNK"


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


def format_discord(e: Event) -> dict:
    token = e.token or "unknown"
    symbol = _symbol_from_event(e)
    short_addr = _short_addr(token)

    if e.type == "promoted":
        reasons = []
        if e.reasons:
            reasons = e.reasons[:4]
        rscore = float(e.extra.get("risk_score") or 0.0) if isinstance(e.extra, dict) else 0.0
        ascore = float(e.extra.get("attention_score") or 0.0) if isinstance(e.extra, dict) else 0.0
        metrics = _extract_metrics(e)
        confidence_bar = render_confidence_bar(e.confidence)
        change_24h = _format_change_pct(metrics.get("price_change_h24"))
        sparkline = render_sparkline(metrics.get("price_points"))
        mc_value = metrics.get("market_cap")
        liq_value = metrics.get("liq")
        liq_mc = "-"
        try:
            if mc_value and float(mc_value) > 0 and liq_value is not None:
                liq_mc = f"{round((float(liq_value) / float(mc_value)) * 100)}%"
        except Exception:
            liq_mc = "-"

        fields = [
            {
                "name": "Overview",
                "value": _section_lines(
                    [
                        f"Token ${symbol}",
                        f"Address `{short_addr}`",
                        f"Final Score {e.confidence:.2f} (0.75)",
                        f"Confidence {confidence_bar} ({int(round(e.confidence * 100))}%)",
                        f"Risk {rscore:.2f}",
                        f"Attention {ascore:.2f}",
                    ]
                ),
                "inline": False,
            }
        ]
        snapshot_lines = [
            f"Liquidity {_fmt_usd(metrics['liq'])}",
            f"Market Cap {_fmt_usd(mc_value)}",
            f"Liq / MC {liq_mc}",
        ]
        if change_24h is not None:
            change_line = f"24h Change {change_24h}"
            if sparkline:
                change_line = f"{change_line}  {sparkline}"
            snapshot_lines.insert(0, change_line)
        fields.append(
            {
                "name": "Market Snapshot",
                "value": _section_lines(snapshot_lines),
                "inline": False,
            }
        )
        fields.append(
            {
                "name": "Wallet Signals",
                "value": _section_lines([_wallet_signal_lines(metrics.get("risk_flags"))]),
                "inline": False,
            }
        )
        if reasons:
            fields.append(
                {
                    "name": "Why Promoted",
                    "value": _section_lines([f"- {r}" for r in reasons]),
                    "inline": False,
                }
            )
        fields.append(
            {
                "name": "Links",
                "value": _section_lines(
                    [
                        f"[Dexscreener](https://dexscreener.com/solana/{token})",
                        f"[Birdeye](https://birdeye.so/token/{token}?chain=solana)",
                    ]
                ),
                "inline": False,
            }
        )

        embed = {
            "title": _promoted_header(e.confidence),
            "description": "Validated by layered gates.",
            "color": DARK_RED,
            "fields": fields,
            "footer": {"text": "Signal Engine  Validated Tier  Size Appropriately"},
        }
        return {"embeds": [embed]}

    conf = f"{e.confidence:.2f}"
    reasons = ", ".join(e.reasons[:4]) if e.reasons else ""
    threshold = None
    if e.type == "heating_up":
        threshold = "0.55"

    lines = [
        f"**{_fmt_title(e)}**",
        f"Token: `{token}`",
        f"Confidence: `{conf}`",
    ]
    if threshold:
        lines.append(f"Threshold: `{threshold}`")
    if e.creator:
        lines.append(f"Creator: `{e.creator}`")
    if e.signature:
        lines.append(f"Sig: `{e.signature}`")
    if reasons:
        lines.append(f"Reasons: {reasons}")

    if isinstance(e.extra, dict) and e.extra:
        if "wallet_risk" in e.extra:
            wr = e.extra["wallet_risk"]
            lines.append(f"Wallet risk: `{wr.get('score', 0):.2f}` flags={wr.get('flags', [])}")
        if "dex" in e.extra:
            lines.append("Dex: enriched")

    return {"content": "\n".join(lines)}


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
        if r.status_code >= 300:
            print("[discord] send failed", r.status_code, r.text[:200])
        else:
            print("[discord] send ok", r.status_code)
    except Exception as ex:
        print("[discord] send exception", ex)


def send_candidate_discord(e: Event) -> None:
    if not ENABLE_DISCORD:
        return
    if not DISCORD_CANDIDATE_WEBHOOK:
        print("[discord] missing DISCORD_CANDIDATE_WEBHOOK")
        return
    if e.type != "candidate":
        return

    token = e.token or "unknown"
    symbol = _symbol_from_event(e)
    short_addr = _short_addr(token)
    attention_score = 0.0
    risk_score = 0.0
    if isinstance(e.extra, dict):
        attention_score = float(e.extra.get("attention_score") or 0.0)
        risk_score = float(e.extra.get("risk_score") or 0.0)

    metrics = _extract_metrics(e)
    confidence_bar = render_confidence_bar(e.confidence)
    change_24h = _format_change_pct(metrics.get("price_change_h24"))
    sparkline = render_sparkline(metrics.get("price_points"))
    mc_value = metrics.get("market_cap")
    liq_value = metrics.get("liq")
    liq_mc = "-"
    try:
        if mc_value and float(mc_value) > 0 and liq_value is not None:
            liq_mc = f"{round((float(liq_value) / float(mc_value)) * 100)}%"
    except Exception:
        liq_mc = "-"
    fields = [
        {
            "name": "Overview",
            "value": _section_lines(
                [
                    f"Token ${symbol}",
                    f"Address `{short_addr}`",
                    f"Attention {attention_score:.2f}",
                    f"Risk {risk_score:.2f}",
                    f"Confidence {confidence_bar} ({int(round(e.confidence * 100))}%)",
                ]
            ),
            "inline": False,
        }
    ]
    snapshot_lines = [
        f"Liquidity {_fmt_usd(metrics['liq'])}",
        f"Market Cap {_fmt_usd(mc_value)}",
        f"Liq / MC {liq_mc}",
    ]
    if change_24h is not None:
        change_line = f"24h Change {change_24h}"
        if sparkline:
            change_line = f"{change_line}  {sparkline}"
        snapshot_lines.insert(0, change_line)
    fields.append(
        {
            "name": "Market Snapshot",
            "value": _section_lines(snapshot_lines),
            "inline": False,
        }
    )
    fields.append(
        {
            "name": "Wallet Signals",
            "value": _section_lines([_wallet_signal_lines(metrics.get("risk_flags"))]),
            "inline": False,
        }
    )
    fields.append(
        {
            "name": "Links",
            "value": _section_lines(
                [
                    f"[Dexscreener](https://dexscreener.com/solana/{token})",
                    f"[Birdeye](https://birdeye.so/token/{token}?chain=solana)",
                ]
            ),
            "inline": False,
        }
    )

    embed = {
        "title": _candidate_header(attention_score, risk_score),
        "description": "Early coordination detected. Watch only.",
        "color": AMBER,
        "fields": fields,
        "footer": {"text": "Signal Engine  Radar Mode  High Risk Market"},
    }
    payload = {"embeds": [embed]}
    print(f"[discord] candidate send attempt token={e.token}", flush=True)

    if DRY_RUN:
        print("[DRY_RUN] suppressed Candidate Discord send", json.dumps(payload)[:400])
        return

    try:
        r = requests.post(DISCORD_CANDIDATE_WEBHOOK, json=payload, timeout=8)
        if r.status_code >= 300:
            print("[discord] candidate send failed", r.status_code, r.text[:200])
        else:
            print("[discord] candidate send ok", r.status_code)
    except Exception as ex:
        print("[discord] candidate send exception", ex)

import json
import requests
from worker.config import (
    ENABLE_DISCORD,
    DISCORD_WEBHOOK_URL,
    DISCORD_CANDIDATE_WEBHOOK,
    DRY_RUN,
)
from worker.events import Event
from worker.config import RADAR_QUIET_RISK_MAX
from worker.metadata import fetch_token_metadata


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


def render_confidence_pct(score: float) -> str:
    blocks = 5
    try:
        clamped = max(0.0, min(1.0, float(score)))
    except Exception:
        clamped = 0.0
    _ = blocks
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


def _candidate_header(attention_score: float, risk_score: float) -> str:
    if attention_score >= 0.85:
        regime = "BREAKOUT"
    elif attention_score >= 0.70:
        regime = "SETUP"
    elif risk_score < RADAR_QUIET_RISK_MAX:
        regime = "WATCH"
    elif risk_score < 0.50:
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


def _conviction_label(attention_score: float, risk_score: float, lifecycle: str) -> str:
    if lifecycle == "dex" and attention_score >= 0.85 and risk_score <= 0.15:
        return "Momentum confirmed"
    if attention_score >= 0.80:
        return "Strong coordination"
    if attention_score >= 0.65:
        return "Early follow-through"
    return "Developing flow"


def _wallet_signal_lines(risk_flags: dict) -> str:
    if not isinstance(risk_flags, dict):
        return "organic holder distribution"
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
    return "\n".join(lines[:5]) if lines else "- organic holder distribution"


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


def _security_lines(e: Event, metrics: dict, risk_score: float) -> str:
    extra = e.extra if isinstance(e.extra, dict) else {}
    lines = [f"- risk_score: {risk_score:.2f}"]
    elite = extra.get("elite_score")
    if elite is not None:
        lines.append(f"- elite_score: {elite}")
    lifecycle = metrics.get("lifecycle")
    if lifecycle:
        lines.append(f"- lifecycle: {lifecycle}")
    buys = metrics.get("txns_m5_buys")
    sells = metrics.get("txns_m5_sells")
    if buys is not None or sells is not None:
        lines.append(f"- m5 flow: B {buys or 0} / S {sells or 0}")
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
    return "\n".join(lines[:5]) if lines else "- early / pending"


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
    if e.token and (not symbol or not name):
        meta = fetch_token_metadata(e.token)
        if meta:
            if not symbol:
                symbol = str(meta.get("symbol") or "").strip().upper()
            if not name:
                name = str(meta.get("name") or "").strip()
    if not symbol:
        symbol = "UNK"
    if not name:
        name = symbol
    return symbol, name


def _display_confidence_score(e: Event, attention_score: float) -> float:
    try:
        raw = float(e.confidence)
    except Exception:
        raw = 0.0
    if e.type in ("candidate", "heating_up"):
        if attention_score > 0:
            return max(0.0, min(1.0, attention_score))
        return max(0.0, min(1.0, raw))
    if raw > 0:
        return max(0.0, min(1.0, raw))
    return 0.0


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
    lines: list[str],
) -> list[str]:
    return [
        f"**{name}**  `${symbol}`",
        f"`{full_addr}`",
        *lines,
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
    attention_score = 0.0
    risk_score = 0.0
    if isinstance(e.extra, dict):
        attention_score = float(e.extra.get("attention_score") or 0.0)
        risk_score = float(e.extra.get("risk_score") or 0.0)

    metrics = _extract_metrics(e)
    confidence_pct = render_confidence_pct(_display_confidence_score(e, attention_score))
    mc_value = metrics.get("market_cap")
    liq_value = metrics.get("liq")
    liq_mc = "—"
    try:
        if mc_value and float(mc_value) > 0 and liq_value is not None:
            liq_mc = f"{round((float(liq_value) / float(mc_value)) * 100)}%"
    except Exception:
        liq_mc = "—"

    fields = [
        _summary_field("Attention", f"`{attention_score:.2f}`"),
        _summary_field("Confidence", f"`{confidence_pct}`"),
        _summary_field("Lifecycle", f"`{_lifecycle_label(metrics.get('lifecycle'))}`"),
        _summary_field("Conviction", f"`{_conviction_label(attention_score, risk_score, str(metrics.get('lifecycle') or ''))}`"),
        {
            "name": "Token",
            "value": _section_lines(
                _build_overview_lines(
                    symbol,
                    name,
                    full_addr,
                    [
                        _label_value("Risk", f"{risk_score:.2f}"),
                    ],
                )
            ),
            "inline": False,
        },
        {
            "name": "Tape",
            "value": _section_lines([_market_tape(metrics)]),
            "inline": False,
        },
        {
            "name": "Market",
            "value": _section_lines(
                _build_market_snapshot_lines(
                    _fmt_usd(metrics["liq"]),
                    _fmt_usd(mc_value),
                    liq_mc,
                )
            ),
            "inline": False,
        },
        {
            "name": "Stats",
            "value": _section_lines([_stats_lines(metrics)]),
            "inline": True,
        },
        {
            "name": "Signals",
            "value": _section_lines([_attention_signal_lines(e, metrics.get("risk_flags"))]),
            "inline": True,
        },
        {
            "name": "Security",
            "value": _section_lines([_security_lines(e, metrics, risk_score)]),
            "inline": True,
        },
        {
            "name": "Why It Triggered",
            "value": _section_lines([_reason_stack(e)]),
            "inline": False,
        },
        {
            "name": "Links",
            "value": _links_lines(token, metrics),
            "inline": False,
        },
    ]

    embed = {
        "title": _candidate_header(attention_score, risk_score),
        "description": f"{description}\n",
        "color": SLATE if attention_score < 0.85 else AMBER,
        "fields": fields,
        "footer": {"text": "Signal Engine / Radar Deck"},
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
        rscore = float(e.extra.get("risk_score") or 0.0) if isinstance(e.extra, dict) else 0.0
        ascore = float(e.extra.get("attention_score") or 0.0) if isinstance(e.extra, dict) else 0.0
        metrics = _extract_metrics(e)
        confidence_pct = render_confidence_pct(_display_confidence_score(e, ascore))
        mc_value = metrics.get("market_cap")
        liq_value = metrics.get("liq")
        liq_mc = "—"
        try:
            if mc_value and float(mc_value) > 0 and liq_value is not None:
                liq_mc = f"{round((float(liq_value) / float(mc_value)) * 100)}%"
        except Exception:
            liq_mc = "—"

        fields = [
            _summary_field("Final Score", f"`{e.confidence:.2f}`"),
            _summary_field("Confidence", f"`{confidence_pct}`"),
            _summary_field("Lifecycle", f"`{_lifecycle_label(metrics.get('lifecycle'))}`"),
            _summary_field("Conviction", f"`{_conviction_label(ascore, rscore, str(metrics.get('lifecycle') or ''))}`"),
            {
                "name": "Token",
                "value": _section_lines(
                    _build_overview_lines(
                        symbol,
                        name,
                        full_addr,
                        [
                            _label_value("Risk", f"{rscore:.2f}"),
                            _label_value("Attention", f"{ascore:.2f}"),
                        ],
                    )
                ),
                "inline": False,
            }
        ]
        fields.append(
            {
                "name": "Tape",
                "value": _section_lines([_market_tape(metrics)]),
                "inline": False,
            }
        )
        fields.append(
            {
                "name": "Market",
                "value": _section_lines(
                    _build_market_snapshot_lines(
                        _fmt_usd(metrics["liq"]),
                        _fmt_usd(mc_value),
                        liq_mc,
                    )
                ),
                "inline": False,
            }
        )
        fields.append(
            {
                "name": "Stats",
                "value": _section_lines([_stats_lines(metrics)]),
                "inline": True,
            }
        )
        fields.append(
            {
                "name": "Signals",
                "value": _section_lines([_attention_signal_lines(e, metrics.get("risk_flags"))]),
                "inline": True,
            }
        )
        fields.append(
            {
                "name": "Security",
                "value": _section_lines([_security_lines(e, metrics, rscore)]),
                "inline": True,
            }
        )
        fields.append(
            {
                "name": "Why It Triggered",
                "value": _section_lines([_reason_stack(e)]),
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
                "value": _links_lines(token, metrics),
                "inline": False,
            }
        )

        embed = {
            "title": _promoted_header(e.confidence),
            "description": "Validated by layered gates.\n",
            "color": DARK_RED,
            "fields": fields,
            "footer": {"text": "Signal Engine / Alpha Deck"},
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

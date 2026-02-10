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
    }


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
        reasons = e.reasons[:4] if e.reasons else []
        reason_lines = "\n".join(f"- {r}" for r in reasons) if reasons else "-"
        rscore = float(e.extra.get("risk_score") or 0.0) if isinstance(e.extra, dict) else 0.0
        ascore = float(e.extra.get("attention_score") or 0.0) if isinstance(e.extra, dict) else 0.0
        metrics = _extract_metrics(e)

        embed = {
            "title": _fmt_title(e),
            "description": "Validated by layered gates.",
            "color": DARK_RED,
            "fields": [
                {"name": "Token", "value": f"${symbol}", "inline": True},
                {"name": "Address", "value": f"`{short_addr}`", "inline": True},
                {
                    "name": "Final Score",
                    "value": f"{e.confidence:.2f}",
                    "inline": True,
                },
                {"name": "Risk", "value": f"{rscore:.2f}", "inline": True},
                {"name": "Attention", "value": f"{ascore:.2f}", "inline": True},
                {"name": "Liquidity", "value": _fmt_usd(metrics["liq"]), "inline": True},
                {"name": "Unique Buyers (15m)", "value": _fmt_num(metrics["unique_buyers_15m"]), "inline": True},
                {"name": "Age", "value": _fmt_age_minutes(metrics["age"]), "inline": True},
                {"name": "Why Promoted", "value": reason_lines, "inline": False},
            ],
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
    reasons = []
    if isinstance(e.extra, dict):
        attention_score = float(e.extra.get("attention_score") or 0.0)
        risk_score = float(e.extra.get("risk_score") or 0.0)
        reasons = e.extra.get("attention_reasons") or []

    metrics = _extract_metrics(e)
    reason_lines = "\n".join(f"- {r}" for r in reasons[:4]) if reasons else "-"

    embed = {
        "title": _fmt_title(e),
        "description": "Early coordination detected. Watch only.",
        "color": AMBER,
        "fields": [
            {"name": "Token", "value": f"${symbol}", "inline": True},
            {"name": "Address", "value": f"`{short_addr}`", "inline": True},
            {"name": "Attention", "value": f"{attention_score:.2f}", "inline": True},
            {"name": "Risk", "value": f"{risk_score:.2f}", "inline": True},
            {"name": "Liquidity", "value": _fmt_usd(metrics["liq"]), "inline": True},
            {"name": "Unique Buyers (5m)", "value": _fmt_num(metrics["unique_buyers_5m"]), "inline": True},
            {"name": "Age", "value": _fmt_age_minutes(metrics["age"]), "inline": True},
            {"name": "Why Flagged", "value": reason_lines, "inline": False},
        ],
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

import json
import requests
from worker.config import (
    ENABLE_DISCORD,
    DISCORD_WEBHOOK_URL,
    DISCORD_CANDIDATE_WEBHOOK,
    DRY_RUN,
    RISK_VETO_THRESHOLD,
)
from worker.events import Event


AMBER = 0xF1C232
DARK_RED = 0x8B0000


def _short_addr(addr: str | None) -> str:
    if not addr:
        return "unknown"
    if len(addr) <= 10:
        return addr
    return f"{addr[:4]}...{addr[-4:]}"


def _symbol_from_event(e: Event) -> str:
    if isinstance(e.extra, dict):
        sym = e.extra.get("symbol")
        if isinstance(sym, str) and sym.strip():
            return sym.strip().upper()
    return "UNK"


def _fmt_title(e: Event) -> str:
    if e.type == "promoted":
        return "🔴 PROMOTED  VALIDATED SIGNAL"
    if e.type == "candidate":
        return "🟡 ATTENTION CANDIDATE  WATCH ONLY"
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
        reason_lines = "\n".join(f"- {r}" for r in reasons) if reasons else "—"
        rscore = float(e.extra.get("risk_score") or 0.0) if isinstance(e.extra, dict) else 0.0
        ascore = float(e.extra.get("attention_score") or 0.0) if isinstance(e.extra, dict) else 0.0

        embed = {
            "title": _fmt_title(e),
            "color": DARK_RED,
            "fields": [
                {"name": "Token", "value": f"${symbol}", "inline": True},
                {"name": "Address", "value": f"`{short_addr}`", "inline": True},
                {
                    "name": "Final Score",
                    "value": f"{e.confidence:.2f} (threshold 0.80)",
                    "inline": False,
                },
                {"name": "Risk Score", "value": f"{rscore:.2f}", "inline": True},
                {"name": "Attention Score", "value": f"{ascore:.2f}", "inline": True},
                {"name": "Why promoted", "value": reason_lines, "inline": False},
                {"name": "Notes", "value": "Not financial advice; size appropriately", "inline": False},
            ],
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

    reason_lines = "\n".join(f"- {r}" for r in reasons[:4]) if reasons else "—"
    risk_label = "below veto" if risk_score < RISK_VETO_THRESHOLD else "high"

    embed = {
        "title": _fmt_title(e),
        "color": AMBER,
        "fields": [
            {"name": "Token", "value": f"${symbol}", "inline": True},
            {"name": "Address", "value": f"`{short_addr}`", "inline": True},
            {"name": "Attention Score", "value": f"{attention_score:.2f}", "inline": True},
            {"name": "Risk Score", "value": f"{risk_score:.2f} ({risk_label})", "inline": True},
            {"name": "Why this pinged", "value": reason_lines, "inline": False},
            {"name": "Status", "value": "WATCH ONLY  HIGH RISK", "inline": False},
            {"name": "Notes", "value": "Not financial advice", "inline": False},
        ],
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

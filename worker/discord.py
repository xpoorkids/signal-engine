import json
import requests
from worker.config import ENABLE_DISCORD, DISCORD_WEBHOOK_URL, DRY_RUN
from worker.events import Event


def _fmt_title(e: Event) -> str:
    if e.type == "promoted":
        return "PROMOTED"
    if e.type == "heating_up":
        return "HEATING UP"
    if e.type.startswith("early"):
        return "EARLY"
    return f"INFO {e.type}"


def format_discord(e: Event) -> dict:
    token = e.token or "unknown"
    conf = f"{e.confidence:.2f}"
    reasons = ", ".join(e.reasons[:4]) if e.reasons else ""

    lines = [
        f"**{_fmt_title(e)}**",
        f"Token: `{token}`",
        f"Confidence: `{conf}`",
    ]
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

    if DRY_RUN:
        print("[DRY_RUN] suppressed Discord send", json.dumps(payload)[:400])
        return

    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=8)
        if r.status_code >= 300:
            print("[discord] send failed", r.status_code, r.text[:200])
    except Exception as ex:
        print("[discord] send exception", ex)

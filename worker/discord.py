import json
import requests
from worker.config import (
    ENABLE_DISCORD,
    DISCORD_WEBHOOK_URL,
    DISCORD_CANDIDATE_WEBHOOK,
    DRY_RUN,
)
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
    threshold = None
    if e.type == "promoted":
        threshold = "0.80"
    elif e.type == "heating_up":
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
        if "risk_score" in e.extra:
            rscore = e.extra.get("risk_score", 0.0)
            rreasons = e.extra.get("risk_reasons", [])[:3]
            lines.append(f"Risk: `{rscore:.2f}` reasons={rreasons}")
        if "attention_score" in e.extra:
            ascore = e.extra.get("attention_score", 0.0)
            areasons = e.extra.get("attention_reasons", [])[:3]
            lines.append(f"Attention: `{ascore:.2f}` reasons={areasons}")
        if "edge_bps" in e.extra:
            edge_bps = e.extra.get("edge_bps", 0.0)
            if edge_bps:
                lines.append(f"Edge: `{edge_bps:.1f}` bps")

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


def send_candidate_discord(e: Event) -> None:
    if not ENABLE_DISCORD:
        return
    if not DISCORD_CANDIDATE_WEBHOOK:
        print("[discord] missing DISCORD_CANDIDATE_WEBHOOK")
        return
    if e.type != "candidate":
        return

    token = e.token or "unknown"
    attention_score = 0.0
    risk_score = 0.0
    reasons = []
    if isinstance(e.extra, dict):
        attention_score = float(e.extra.get("attention_score") or 0.0)
        risk_score = float(e.extra.get("risk_score") or 0.0)
        reasons = e.extra.get("attention_reasons") or []

    lines = [
        "**WATCH ONLY / HIGH RISK**",
        f"Token: `{token}`",
        f"Attention: `{attention_score:.2f}`",
        f"Risk: `{risk_score:.2f}`",
    ]
    if reasons:
        lines.append(f"Attention reasons: {', '.join(reasons[:3])}")

    payload = {"content": "\n".join(lines)}

    if DRY_RUN:
        print("[DRY_RUN] suppressed Candidate Discord send", json.dumps(payload)[:400])
        return

    try:
        r = requests.post(DISCORD_CANDIDATE_WEBHOOK, json=payload, timeout=8)
        if r.status_code >= 300:
            print("[discord] candidate send failed", r.status_code, r.text[:200])
    except Exception as ex:
        print("[discord] candidate send exception", ex)

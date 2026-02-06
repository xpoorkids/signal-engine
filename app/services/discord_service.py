import itertools
import os
from datetime import datetime, timezone

import requests

WEBHOOKS = {
    "near_pass": [os.getenv("DISCORD_WEBHOOK_NEAR_PASS")],
    "pass": [os.getenv("DISCORD_WEBHOOK_PASS")],
    "rug": [os.getenv("DISCORD_WEBHOOK_RUG")],
    "logs": [os.getenv("DISCORD_WEBHOOK_LOGS")],
    "digest": [os.getenv("DISCORD_WEBHOOK_DIGEST")],
}

for k in WEBHOOKS:
    WEBHOOKS[k] = [w for w in WEBHOOKS[k] if w]

_ROUND_ROBIN = {k: itertools.cycle(v) for k, v in WEBHOOKS.items() if v}

COLORS = {
    "near_pass": 0xF1C40F,
    "pass": 0x2ECC71,
    "rug": 0xE74C3C,
    "logs": 0x95A5A6,
    "digest": 0x3498DB,
}

HEADERS = {
    "near_pass": "🟡 NEAR-PASS DETECTED",
    "pass": "🟢 PASS CONFIRMED",
    "rug": "🔴 RUG RISK FLAGGED",
    "logs": " ENGINE STATUS",
    "digest": "📊 DAILY SIGNAL DIGEST",
}


def _bar(value, max_value, length=10):
    pct = min(max(value / max_value, 0), 1)
    filled = int(pct * length)
    return "" * filled + "" * (length - filled)


def _post(url: str, payload: dict):
    try:
        requests.post(url, json=payload, timeout=6)
    except Exception:
        pass


def _send(embed: dict, mode: str):
    hooks = WEBHOOKS.get(mode, [])
    if not hooks:
        return
    _post(next(_ROUND_ROBIN[mode]), {"embeds": [embed]})


def send_text(text: str, mode: str = "logs", fanout: bool = False):
    hooks = WEBHOOKS.get(mode, [])
    if not hooks:
        return
    if fanout:
        for h in hooks:
            _post(h, {"content": text})
    else:
        _post(next(_ROUND_ROBIN[mode]), {"content": text})


def _confidence_score(m: dict) -> int:
    liq = m.get("liquidity", 0)
    vol = m.get("volume_5m", 0)
    mom = abs(m.get("price_change_5m", 0))
    age = m.get("age_minutes", 999)

    score = 0
    score += min(liq / 20000, 1) * 35
    score += min(vol / 10000, 1) * 35
    score += min(mom / 10, 1) * 20
    score += max(0, (240 - age) / 240) * 10

    return int(round(score))


def _wallet_badge(wallet: dict | None) -> tuple[str, str]:
    if not wallet or not wallet.get("enabled"):
        return ("Unknown", "")

    risk = wallet.get("risk")
    if risk == "ok":
        return ("Wallet OK", "🟢")
    if risk == "warn":
        return ("Wallet Warn", "🟡")
    if risk == "high":
        return ("Wallet High Risk", "🔴")

    return ("Unknown", "")


def send_candidate(candidate: dict, mode: str, explanation: str):
    m = candidate.get("metrics", {})
    sym = candidate.get("symbol", "UNK")
    token = candidate.get("token", "")
    wallet = candidate.get("wallet")
    escalated = candidate.get("escalated_from") == "near_pass"

    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    liq = m.get("liquidity", 0)
    vol = m.get("volume_5m", 0)
    mom = m.get("price_change_5m", 0)
    age = m.get("age_minutes", 0)

    conf = _confidence_score(m)
    wallet_label, wallet_emoji = _wallet_badge(wallet)

    dex_url = f"https://dexscreener.com/solana/{token}"
    sol_url = f"https://solscan.io/token/{token}"

    fields = [
        {
            "name": "💧 Liquidity",
            "value": f"${liq:,.0f}\n`{_bar(liq, 50000)}`",
            "inline": True,
        },
        {
            "name": "📊 Volume (5m)",
            "value": f"${vol:,.0f}\n`{_bar(vol, 20000)}`",
            "inline": True,
        },
        {
            "name": " Momentum",
            "value": f"{mom:+.2f}%\n`{_bar(abs(mom), 15)}`",
            "inline": True,
        },
        {
            "name": "🎯 Confidence",
            "value": f"{conf}%\n`{_bar(conf, 100)}`",
            "inline": True,
        },
        {
            "name": "🧬 Wallet Risk",
            "value": f"{wallet_emoji} {wallet_label}",
            "inline": True,
        },
    ]

    if escalated:
        fields.append(
            {
                "name": " Escalation",
                "value": "Promoted from **Near-Pass** after confirmations",
                "inline": False,
            }
        )

    fields.extend(
        [
            {
                "name": "🧠 Why this hit",
                "value": explanation,
                "inline": False,
            },
            {
                "name": "🔗 Links",
                "value": f"[Dexscreener]({dex_url})  [Solscan]({sol_url})",
                "inline": False,
            },
        ]
    )

    embed = {
        "title": HEADERS.get(mode, "SIGNAL"),
        "description": f"**SOL  ${sym}**",
        "color": COLORS.get(mode, 0xFFFFFF),
        "fields": fields,
        "footer": {"text": f"signal-engine  {now}"},
    }

    _send(embed, mode)


def send_collapsed_repeat(
    candidate: dict,
    mode: str,
    stats: dict,
    heating_up: bool = False,
):
    sym = candidate.get("symbol", "UNK")
    token = candidate.get("token", "")
    first_seen = stats.get("first_seen")
    last_seen = stats.get("last_seen")
    repeat_count = stats.get("repeat_count", 1)

    def fmt(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S UTC")

    title = f"{HEADERS.get(mode, 'SIGNAL')} (REPEATED)"
    if heating_up:
        title += " 🔥"

    embed = {
        "title": title,
        "description": f"**SOL  ${sym}**",
        "color": COLORS.get(mode, 0xFFFFFF),
        "fields": [
            {
                "name": "🔁 Repeats",
                "value": f"{repeat_count}",
                "inline": True,
            },
            {
                "name": " First Seen",
                "value": fmt(first_seen) if first_seen else "",
                "inline": True,
            },
            {
                "name": " Last Seen",
                "value": fmt(last_seen) if last_seen else "",
                "inline": True,
            },
            {
                "name": "🔗 Links",
                "value": (
                    f"[Dexscreener](https://dexscreener.com/solana/{token})  "
                    f"[Solscan](https://solscan.io/token/{token})"
                ),
                "inline": False,
            },
        ],
        "footer": {
            "text": "signal-engine  collapsed repeat"
        },
    }

    _send(embed, mode)

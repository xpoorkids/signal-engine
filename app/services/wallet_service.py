import os

import requests

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "").strip()

TOP_HOLDER_WARN = float(os.getenv("WALLET_TOP_HOLDER_WARN", "0.08"))
TOP10_WARN = float(os.getenv("WALLET_TOP10_WARN", "0.35"))


def _helius_url():
    if HELIUS_RPC_URL:
        if "api-key=" in HELIUS_RPC_URL or "apikey=" in HELIUS_RPC_URL or not HELIUS_API_KEY:
            return HELIUS_RPC_URL
        sep = "&" if "?" in HELIUS_RPC_URL else "?"
        return f"{HELIUS_RPC_URL}{sep}api-key={HELIUS_API_KEY}"
    if not HELIUS_API_KEY:
        return ""
    return f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


def wallet_risk_score(token_mint: str) -> dict:
    helius_url = _helius_url()
    if not helius_url:
        return {
            "enabled": False,
            "top_holder_pct": None,
            "top10_pct": None,
            "risk": "ok",
            "reason": "helius_disabled",
        }

    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "getTokenLargestAccounts",
        "params": [token_mint],
    }

    try:
        r = requests.post(helius_url, json=payload, timeout=12)
        r.raise_for_status()
        result = r.json().get("result", {})
    except Exception:
        return {
            "enabled": True,
            "top_holder_pct": None,
            "top10_pct": None,
            "risk": "warn",
            "reason": "helius_unavailable",
        }
    accounts = result.get("value", []) or []

    amounts = []
    for a in accounts[:10]:
        amt = a.get("uiAmount")
        if amt is None:
            amt = a.get("amount")
        try:
            amounts.append(float(amt))
        except Exception:
            pass

    if not amounts:
        return {
            "enabled": True,
            "top_holder_pct": None,
            "top10_pct": None,
            "risk": "warn",
            "reason": "no_holder_data",
        }

    total_top10 = sum(amounts)
    top1 = amounts[0]
    top1_pct = top1 / total_top10 if total_top10 > 0 else None
    top10_pct = 1.0

    risk = "ok"
    reason = "holder_ok"
    if top1_pct is not None and top1_pct >= TOP_HOLDER_WARN:
        risk = "warn"
        reason = f"top1_concentrated_norm({top1_pct:.2f})"
    if top1_pct is not None and top1_pct >= (TOP_HOLDER_WARN * 1.5):
        risk = "high"
        reason = f"top1_high_norm({top1_pct:.2f})"

    return {
        "enabled": True,
        "top_holder_pct": float(top1_pct) if top1_pct is not None else None,
        "top10_pct": float(top10_pct),
        "risk": risk,
        "reason": reason,
    }

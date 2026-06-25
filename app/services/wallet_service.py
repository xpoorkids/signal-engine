import os
import time

import requests

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "").strip()
WALLET_RISK_CACHE_TTL_SEC = int(os.getenv("WALLET_RISK_CACHE_TTL_SEC", "120"))

TOP_HOLDER_WARN = float(os.getenv("WALLET_TOP_HOLDER_WARN", "0.08"))
TOP10_WARN = float(os.getenv("WALLET_TOP10_WARN", "0.35"))
_CACHE: dict[str, tuple[float, dict]] = {}


def _helius_url():
    if HELIUS_RPC_URL:
        if "api-key=" in HELIUS_RPC_URL or "apikey=" in HELIUS_RPC_URL or not HELIUS_API_KEY:
            return HELIUS_RPC_URL
        sep = "&" if "?" in HELIUS_RPC_URL else "?"
        return f"{HELIUS_RPC_URL}{sep}api-key={HELIUS_API_KEY}"
    if not HELIUS_API_KEY:
        return ""
    return f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


def _rpc_post(helius_url: str, payload: dict) -> dict:
    r = requests.post(helius_url, json=payload, timeout=12)
    r.raise_for_status()
    return r.json().get("result", {}) or {}


def _amount_float(item: dict) -> float | None:
    value = item.get("uiAmount")
    if value is None:
        value = item.get("uiAmountString")
    if value is None:
        value = item.get("amount")
    try:
        return float(value)
    except Exception:
        return None


def wallet_risk_score(token_mint: str) -> dict:
    cached = _CACHE.get(token_mint)
    now = time.time()
    if cached and cached[0] > now:
        return dict(cached[1])

    helius_url = _helius_url()
    if not helius_url:
        result = {
            "enabled": False,
            "top_holder_pct": None,
            "top10_pct": None,
            "risk": None,
            "reason": "helius_disabled",
            "status": "disabled",
        }
        _CACHE[token_mint] = (now + WALLET_RISK_CACHE_TTL_SEC, result)
        return dict(result)

    largest_payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "getTokenLargestAccounts",
        "params": [token_mint],
    }
    supply_payload = {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "getTokenSupply",
        "params": [token_mint],
    }

    try:
        largest_result = _rpc_post(helius_url, largest_payload)
        supply_result = _rpc_post(helius_url, supply_payload)
    except Exception:
        output = {
            "enabled": True,
            "top_holder_pct": None,
            "top10_pct": None,
            "risk": None,
            "reason": "helius_unavailable",
            "status": "insufficient_data",
        }
        _CACHE[token_mint] = (now + WALLET_RISK_CACHE_TTL_SEC, output)
        return dict(output)
    accounts = largest_result.get("value", []) or []
    supply_value = supply_result.get("value") if isinstance(supply_result.get("value"), dict) else {}
    total_supply = _amount_float(supply_value)

    amounts = []
    for a in accounts[:10]:
        amount = _amount_float(a)
        if amount is not None:
            amounts.append(amount)

    if not amounts or total_supply is None or total_supply <= 0:
        output = {
            "enabled": True,
            "top_holder_pct": None,
            "top10_pct": None,
            "risk": None,
            "reason": "no_holder_supply_data" if amounts else "no_holder_data",
            "status": "insufficient_data",
        }
        _CACHE[token_mint] = (now + WALLET_RISK_CACHE_TTL_SEC, output)
        return dict(output)

    total_top10 = sum(amounts)
    top1 = amounts[0]
    top1_pct = top1 / total_supply
    top10_pct = total_top10 / total_supply

    risk = "ok"
    reason = "holder_ok"
    if top1_pct is not None and top1_pct >= TOP_HOLDER_WARN:
        risk = "warn"
        reason = f"top1_concentrated_norm({top1_pct:.2f})"
    if top1_pct is not None and top1_pct >= (TOP_HOLDER_WARN * 1.5):
        risk = "high"
        reason = f"top1_high_norm({top1_pct:.2f})"

    output = {
        "enabled": True,
        "top_holder_pct": float(top1_pct) if top1_pct is not None else None,
        "top10_pct": float(top10_pct),
        "risk": risk,
        "reason": reason,
        "status": "computed",
    }
    _CACHE[token_mint] = (now + WALLET_RISK_CACHE_TTL_SEC, output)
    return dict(output)

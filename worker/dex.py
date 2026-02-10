import os
import time
from typing import Any, Optional, Dict

import requests


DEX_BASE = os.getenv("DEX_BASE", "https://api.dexscreener.com/latest").rstrip("/")


async def dex_enrich_token(token: str) -> dict:
    try:
        r = requests.get(f"{DEX_BASE}/dex/tokens/{token}", timeout=8)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                pairs = data.get("pairs")
                has_pairs = isinstance(pairs, list) and len(pairs) > 0
                data.setdefault("ok", has_pairs)
            return data
    except Exception:
        pass
    return {"ok": False}


def _pair_matches_token(pair: Dict[str, Any], token: str) -> bool:
    if not token:
        return False
    base = pair.get("baseToken", {}) if isinstance(pair.get("baseToken"), dict) else {}
    quote = pair.get("quoteToken", {}) if isinstance(pair.get("quoteToken"), dict) else {}
    base_addr = base.get("address")
    quote_addr = quote.get("address")
    return base_addr == token or quote_addr == token


def select_best_pair(dex_data: Dict[str, Any], token: str) -> Optional[Dict[str, Any]]:
    pairs = dex_data.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return None

    best = None
    best_liq = -1.0
    for p in pairs:
        if not isinstance(p, dict):
            continue
        if p.get("chainId") not in (None, "solana"):
            continue
        if token and not _pair_matches_token(p, token):
            continue
        liq = 0.0
        try:
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
        except Exception:
            liq = 0.0
        if liq > best_liq:
            best_liq = liq
            best = p
    return best


def summarize_pair(pair: Dict[str, Any]) -> Dict[str, Any]:
    now_ms = time.time() * 1000
    created = pair.get("pairCreatedAt")
    age_minutes = None
    try:
        if created:
            age_minutes = round((now_ms - float(created)) / 60000.0, 2)
    except Exception:
        age_minutes = None

    liq = (pair.get("liquidity") or {}).get("usd")
    vol = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}

    m5_txns = txns.get("m5") or {}

    return {
        "pair_address": pair.get("pairAddress"),
        "dex_id": pair.get("dexId"),
        "liquidity_usd": liq,
        "volume_m5": vol.get("m5"),
        "volume_h1": vol.get("h1"),
        "txns_m5_buys": m5_txns.get("buys"),
        "txns_m5_sells": m5_txns.get("sells"),
        "price_change_m5": price_change.get("m5"),
        "price_change_h1": price_change.get("h1"),
        "age_minutes": age_minutes,
        "fdv": pair.get("fdv"),
    }

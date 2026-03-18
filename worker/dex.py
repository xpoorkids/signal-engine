"""
DEX enrichment and pair selection for token routing decisions.

Purpose
-------
- Fetches DexScreener data for a token and reduces multi-pair responses into a
  single summary used by `worker.promote`.
- Supplies the main DEX-backed market context for:
  - candidate admission on the DEX lifecycle
  - promoted eligibility
  - Discord link selection and market snapshot fields

Runtime data flow
-----------------
Inputs:
- Token mint address.
- DexScreener HTTP response from `/dex/tokens/{token}`.

Transformations:
1. `dex_enrich_token()` fetches the raw DexScreener payload.
2. `select_best_pair()` filters to Solana pairs that actually reference the
   token and chooses the pair with the highest USD liquidity.
3. `summarize_pair()` normalizes the selected pair into a compact structure
   with liquidity, volume, tx counts, price change, age, FDV/market cap, and
   social/website links.

Outputs:
- Raw DEX payload attached to `Event.extra["dex"]`.
- Normalized pair summary attached to `Event.extra["dex_summary"]` by
  `worker.promote`.

Key logic
---------
- Pair selection is liquidity-first. The highest-liquidity Solana pair wins.
- Pair matching accepts the token as either base or quote token, which matters
  for quote-token-oriented pair layouts.
- `dex_enrich_token()` marks `ok=True` only when DexScreener returns at least
  one pair; otherwise downstream code should treat the token as non-DEX or not
  yet listed.
- Age is derived from `pairCreatedAt` at read time, so it reflects current wall
  clock rather than a persisted snapshot.

Failure modes
-------------
- DexScreener unavailable or timeout:
  - `dex_enrich_token()` returns `{"ok": False}`.
  - Downstream candidate logic may fall back to the bonding-curve lifecycle.
- Non-Solana or malformed pairs:
  - `select_best_pair()` ignores them.
- Pair exists but summary fields are sparse:
  - `summarize_pair()` returns partial values; downstream gates may then fail on
    missing age/liquidity/volume conditions.

Logging and observability
-------------------------
- This module does not log directly.
- Operational visibility appears later in `worker.promote` via:
  - `[gate-skip]`
  - `[promotion-block]`
  - Discord market snapshot fields derived from `dex_summary`

Dependencies and config
-----------------------
External dependencies:
- DexScreener HTTP API
- `requests`

Important config inputs:
- `DEX_BASE`

Gotchas
-------
- The "best pair" is not necessarily the newest or most relevant socially; it
  is simply the highest-liquidity matching Solana pair.
- Returning `{"ok": False}` does not distinguish between "not listed yet" and
  "DexScreener request failed". Downstream logic must infer lifecycle from the
  broader context.
"""

import os
import time
from typing import Any, Optional, Dict

import requests


DEX_BASE = os.getenv("DEX_BASE", "https://api.dexscreener.com/latest").rstrip("/")


async def dex_enrich_token(token: str) -> dict:
    """
    Fetch the raw DexScreener token payload and mark whether any pairs were
    present.
    """
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
    """
    Choose the highest-liquidity Solana pair that actually references the token.
    """
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
    """
    Normalize one DexScreener pair into the compact summary consumed by routing,
    gating, and Discord rendering.
    """
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
    info = pair.get("info") or {}
    socials = info.get("socials") or []
    websites = info.get("websites") or []

    website_url = None
    twitter_url = None
    telegram_url = None
    for item in websites:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").lower()
        url = item.get("url")
        if isinstance(url, str) and url:
            if website_url is None or label == "website":
                website_url = url
    for item in socials:
        if not isinstance(item, dict):
            continue
        social_type = str(item.get("type") or "").lower()
        url = item.get("url")
        if not isinstance(url, str) or not url:
            continue
        if social_type in ("twitter", "x") and not twitter_url:
            twitter_url = url
        elif social_type == "telegram" and not telegram_url:
            telegram_url = url

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
        "price_change_h24": price_change.get("h24"),
        "age_minutes": age_minutes,
        "fdv": pair.get("fdv"),
        "market_cap": pair.get("marketCap"),
        "website_url": website_url,
        "twitter_url": twitter_url,
        "telegram_url": telegram_url,
    }

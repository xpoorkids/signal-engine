import os
from typing import Iterable

import requests

DEX_URL = "https://api.dexscreener.com/latest/dex/search?q=solana"
DEX_TOKEN_BATCH_URL = "https://api.dexscreener.com/latest/dex/tokens/{tokens}"
DEX_PROFILE_URLS = (
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
    "https://api.dexscreener.com/token-boosts/top/v1",
)
DEFAULT_SEARCH_QUERIES = ["solana", "pump", "raydium", "moonshot", "bonk", "trenches"]


def _search_queries() -> list[str]:
    raw = os.getenv("DEX_SEARCH_QUERIES", "").strip()
    if not raw:
        return DEFAULT_SEARCH_QUERIES
    return [item.strip() for item in raw.split(",") if item.strip()]


def _solana_token_addresses(items: Iterable[dict]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or str(item.get("chainId") or "").lower() != "solana":
            continue
        token = str(item.get("tokenAddress") or "").strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _fetch_json(url: str):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def _fetch_search_pairs() -> list[dict]:
    pairs: list[dict] = []
    seen_pairs: set[str] = set()
    for query in _search_queries():
        data = _fetch_json(f"https://api.dexscreener.com/latest/dex/search?q={query}")
        for pair in data.get("pairs", []) if isinstance(data, dict) else []:
            if not isinstance(pair, dict):
                continue
            pair_key = str(pair.get("pairAddress") or "") or repr(
                (
                    pair.get("chainId"),
                    (pair.get("baseToken") or {}).get("address") if isinstance(pair.get("baseToken"), dict) else None,
                    (pair.get("quoteToken") or {}).get("address") if isinstance(pair.get("quoteToken"), dict) else None,
                )
            )
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            pairs.append(pair)
    return pairs


def _fetch_profile_pairs() -> list[dict]:
    tokens: list[str] = []
    seen_tokens: set[str] = set()
    for url in DEX_PROFILE_URLS:
        data = _fetch_json(url)
        items = data if isinstance(data, list) else []
        for token in _solana_token_addresses(items):
            if token not in seen_tokens:
                seen_tokens.add(token)
                tokens.append(token)

    pairs: list[dict] = []
    seen_pairs: set[str] = set()
    for start in range(0, len(tokens), 30):
        batch = tokens[start : start + 30]
        if not batch:
            continue
        data = _fetch_json(DEX_TOKEN_BATCH_URL.format(tokens=",".join(batch)))
        for pair in data.get("pairs", []) if isinstance(data, dict) else []:
            if not isinstance(pair, dict):
                continue
            pair_key = str(pair.get("pairAddress") or "")
            if pair_key and pair_key in seen_pairs:
                continue
            if pair_key:
                seen_pairs.add(pair_key)
            pairs.append(pair)
    return pairs


def fetch_solana_pairs():
    pairs = []
    try:
        pairs.extend(_fetch_search_pairs())
    except Exception as exc:
        print(f"[dex] search fetch failed: {type(exc).__name__}: {exc}", flush=True)
    try:
        pairs.extend(_fetch_profile_pairs())
    except Exception as exc:
        print(f"[dex] profile fetch failed: {type(exc).__name__}: {exc}", flush=True)
    print(f"[dex] fetched {len(pairs)} pairs", flush=True)
    return pairs

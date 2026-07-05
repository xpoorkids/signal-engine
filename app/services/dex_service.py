import os
from typing import Iterable

import requests

DEX_URL = "https://api.dexscreener.com/latest/dex/search?q=solana"
DEX_TOKEN_BATCH_URL = "https://api.dexscreener.com/tokens/v1/solana/{tokens}"
DEX_DISCOVERY_URLS = (
    ("https://api.dexscreener.com/token-profiles/latest/v1", "token_profile"),
    ("https://api.dexscreener.com/community-takeovers/latest/v1", "community_takeover"),
    ("https://api.dexscreener.com/ads/latest/v1", "paid_ad"),
    ("https://api.dexscreener.com/token-boosts/latest/v1", "token_boost_latest"),
    ("https://api.dexscreener.com/token-boosts/top/v1", "token_boost_top"),
)
DEFAULT_SEARCH_QUERIES = ["solana", "pump", "raydium", "moonshot", "bonk", "trenches"]


def _search_queries() -> list[str]:
    raw = os.getenv("DEX_SEARCH_QUERIES", "").strip()
    if not raw:
        return DEFAULT_SEARCH_QUERIES
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_token_address(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if "/coin/" in token:
        token = token.rsplit("/coin/", 1)[-1]
    token = token.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    if not token or "/" in token:
        return ""
    if len(token) < 32 or len(token) > 64:
        return ""
    return token


def _external_seed_tokens() -> list[str]:
    raw = os.getenv("SIGNAL_ENGINE_EXTERNAL_SEED_TOKENS", "").strip()
    if not raw:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for item in raw.replace("\n", ",").split(","):
        token = _normalize_token_address(item)
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _solana_token_addresses(items: Iterable[dict]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or str(item.get("chainId") or "").lower() != "solana":
            continue
        token = _normalize_token_address(item.get("tokenAddress") or item.get("url"))
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _pick_pair_token(pair: dict) -> str | None:
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}
    for value in (base.get("address"), quote.get("address")):
        token = str(value or "").strip()
        if token and token != "So11111111111111111111111111111111111111112":
            return token
    return None


def _fetch_json(url: str):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def _response_pairs(data) -> list[dict]:
    pairs = data.get("pairs") if isinstance(data, dict) else data
    return [pair for pair in pairs if isinstance(pair, dict)] if isinstance(pairs, list) else []


def _fetch_search_pairs() -> list[dict]:
    pairs: list[dict] = []
    seen_pairs: set[str] = set()
    for query in _search_queries():
        data = _fetch_json(f"https://api.dexscreener.com/latest/dex/search?q={query}")
        for pair in _response_pairs(data):
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
    sources_by_token: dict[str, set[str]] = {}
    for url, source in DEX_DISCOVERY_URLS:
        data = _fetch_json(url)
        items = data if isinstance(data, list) else []
        for token in _solana_token_addresses(items):
            sources_by_token.setdefault(token, set()).add(source)
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
        for pair in _response_pairs(data):
            if not isinstance(pair, dict):
                continue
            pair_key = str(pair.get("pairAddress") or "")
            if pair_key and pair_key in seen_pairs:
                continue
            if pair_key:
                seen_pairs.add(pair_key)
            token = _pick_pair_token(pair)
            if token and token in sources_by_token:
                pair["signal_engine_sources"] = sorted(sources_by_token[token])
            pairs.append(pair)
    return pairs


def _fetch_external_seed_pairs() -> list[dict]:
    tokens = _external_seed_tokens()
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
            pair["signal_engine_source"] = "external_seed"
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
    try:
        pairs.extend(_fetch_external_seed_pairs())
    except Exception as exc:
        print(f"[dex] external seed fetch failed: {type(exc).__name__}: {exc}", flush=True)
    print(f"[dex] fetched {len(pairs)} pairs", flush=True)
    return pairs

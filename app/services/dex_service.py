import os
import time
from typing import Iterable

import requests

from app.services.j7tracker_service import fetch_j7tracker_tokens, get_j7tracker_health

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
LAST_SOURCE_HEALTH: dict[str, object] = {
    "last_started_ts": None,
    "last_finished_ts": None,
    "total_pairs": 0,
    "sources": {},
    "errors": {},
}


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


def _source_item(source: str, *, ok: bool, pair_count: int = 0, token_count: int | None = None, error: Exception | None = None) -> dict:
    item: dict[str, object] = {
        "ok": ok,
        "pair_count": int(pair_count or 0),
        "last_finished_ts": time.time(),
    }
    if token_count is not None:
        item["token_count"] = int(token_count or 0)
    if error is not None:
        item["error_type"] = type(error).__name__
        item["error"] = str(error)[:240]
    return item


def get_dex_source_health() -> dict[str, object]:
    return {
        "last_started_ts": LAST_SOURCE_HEALTH.get("last_started_ts"),
        "last_finished_ts": LAST_SOURCE_HEALTH.get("last_finished_ts"),
        "total_pairs": LAST_SOURCE_HEALTH.get("total_pairs", 0),
        "sources": dict(LAST_SOURCE_HEALTH.get("sources") or {}),
        "errors": dict(LAST_SOURCE_HEALTH.get("errors") or {}),
    }


def _fetch_search_pairs() -> tuple[list[dict], dict[str, dict]]:
    pairs: list[dict] = []
    seen_pairs: set[str] = set()
    health: dict[str, dict] = {}
    for query in _search_queries():
        source_key = f"search:{query}"
        try:
            data = _fetch_json(f"https://api.dexscreener.com/latest/dex/search?q={query}")
        except Exception as exc:
            health[source_key] = _source_item(source_key, ok=False, error=exc)
            continue
        count = 0
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
            pair["signal_engine_sources"] = list(
                dict.fromkeys([*pair.get("signal_engine_sources", []), source_key])
            ) if isinstance(pair.get("signal_engine_sources"), list) else [source_key]
            pairs.append(pair)
            count += 1
        health[source_key] = _source_item(source_key, ok=True, pair_count=count)
    return pairs, health


def _fetch_profile_pairs() -> tuple[list[dict], dict[str, dict]]:
    tokens: list[str] = []
    seen_tokens: set[str] = set()
    sources_by_token: dict[str, set[str]] = {}
    health: dict[str, dict] = {}
    for url, source in DEX_DISCOVERY_URLS:
        try:
            data = _fetch_json(url)
        except Exception as exc:
            health[source] = _source_item(source, ok=False, error=exc)
            continue
        items = data if isinstance(data, list) else []
        source_tokens = 0
        for token in _solana_token_addresses(items):
            sources_by_token.setdefault(token, set()).add(source)
            if token not in seen_tokens:
                seen_tokens.add(token)
                tokens.append(token)
            source_tokens += 1
        health[source] = _source_item(source, ok=True, token_count=source_tokens)

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
    for source in list(health):
        token_count = int(health[source].get("token_count") or 0)
        pair_count = sum(1 for pair in pairs if source in (pair.get("signal_engine_sources") or []))
        health[source]["pair_count"] = pair_count
        health[source]["token_count"] = token_count
    return pairs, health


def _fetch_external_seed_pairs() -> tuple[list[dict], dict[str, dict]]:
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
            pair["signal_engine_sources"] = list(
                dict.fromkeys([*pair.get("signal_engine_sources", []), "external_seed"])
            ) if isinstance(pair.get("signal_engine_sources"), list) else ["external_seed"]
            pairs.append(pair)
    return pairs, {"external_seed": _source_item("external_seed", ok=True, pair_count=len(pairs), token_count=len(tokens))}


def _fetch_j7tracker_pairs() -> tuple[list[dict], dict[str, dict]]:
    tokens = fetch_j7tracker_tokens()
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
            pair["signal_engine_sources"] = list(
                dict.fromkeys([*pair.get("signal_engine_sources", []), "j7tracker"])
            ) if isinstance(pair.get("signal_engine_sources"), list) else ["j7tracker"]
            pairs.append(pair)
    health = get_j7tracker_health()
    return pairs, {
        "j7tracker": {
            **_source_item("j7tracker", ok=True, pair_count=len(pairs), token_count=len(tokens)),
            "enabled": bool(health.get("enabled")),
            "configured": bool(health.get("configured")),
            "last_error": health.get("last_error"),
        }
    }


def fetch_solana_pairs():
    pairs = []
    started = time.time()
    source_health: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for label, fetcher in (
        ("search", _fetch_search_pairs),
        ("profile", _fetch_profile_pairs),
        ("external_seed", _fetch_external_seed_pairs),
        ("j7tracker", _fetch_j7tracker_pairs),
    ):
        try:
            fetched, health = fetcher()
            pairs.extend(fetched)
            source_health.update(health)
        except Exception as exc:
            errors[label] = f"{type(exc).__name__}: {exc}"[:240]
            source_health[label] = _source_item(label, ok=False, error=exc)
            print(f"[dex] {label} fetch failed: {type(exc).__name__}: {exc}", flush=True)
    LAST_SOURCE_HEALTH.update(
        {
            "last_started_ts": started,
            "last_finished_ts": time.time(),
            "total_pairs": len(pairs),
            "sources": source_health,
            "errors": errors,
        }
    )
    print(f"[dex] fetched {len(pairs)} pairs", flush=True)
    return pairs

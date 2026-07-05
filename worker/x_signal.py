import time
from typing import Any, Optional

import requests

from worker.config import (
    X_BEARER_TOKEN,
    X_HEAVY_AUTHOR_IDS,
    X_HEAVY_HANDLES,
    X_QUERY_TEMPLATE,
    X_SEARCH_MAX_RESULTS,
)
from worker.metadata import fetch_token_metadata

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 1800
_LAST_HEALTH: dict[str, Any] = {
    "last_query_ts": None,
    "last_success_ts": None,
    "last_error_ts": None,
    "last_token": None,
    "last_status_code": None,
    "last_result_count": None,
    "last_error": None,
    "cache_size": 0,
}


def get_x_signal_health() -> dict[str, Any]:
    return {
        **_LAST_HEALTH,
        "configured": bool(X_BEARER_TOKEN),
        "heavy_handles_count": len(X_HEAVY_HANDLES),
        "heavy_author_ids_count": len(X_HEAVY_AUTHOR_IDS),
        "cache_size": len(_CACHE),
    }


def _build_query(token: str, symbol: str, name: str) -> str:
    clauses = [token]
    if symbol:
        clauses.append(f"${symbol}")
    if name:
        clauses.append(f'"{name}"')
    default_query = "(" + " OR ".join(clauses) + ") -is:retweet -is:reply lang:en"
    query = X_QUERY_TEMPLATE or default_query
    try:
        rendered = query.format(token=token, symbol=symbol, name=name)
    except Exception:
        rendered = default_query
    if token and token not in rendered:
        rendered = f"({token}) OR ({rendered})"
    return rendered


def _user_lookup(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    users = includes.get("users") if isinstance(includes.get("users"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for user in users:
        if not isinstance(user, dict):
            continue
        user_id = str(user.get("id") or "").strip()
        if user_id:
            out[user_id] = user
    return out


def fetch_x_signal(token: str, symbol: str = "", name: str = "") -> Optional[dict[str, Any]]:
    if not X_BEARER_TOKEN or not token:
        _LAST_HEALTH.update(
            {
                "last_query_ts": time.time(),
                "last_token": token,
                "last_error": "not_configured_or_missing_token",
                "last_status_code": None,
                "last_result_count": None,
            }
        )
        return None
    now = time.time()
    cached = _CACHE.get(token)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        _LAST_HEALTH.update(
            {
                "last_query_ts": now,
                "last_success_ts": now,
                "last_token": token,
                "last_status_code": "cache",
                "last_result_count": int(cached[1].get("tweet_count") or 0),
                "last_error": None,
                "cache_size": len(_CACHE),
            }
        )
        return cached[1]

    if not symbol or not name:
        meta = fetch_token_metadata(token)
        if meta:
            symbol = symbol or str(meta.get("symbol") or "").strip()
            name = name or str(meta.get("name") or "").strip()
    if not symbol and not name:
        _LAST_HEALTH.update(
            {
                "last_query_ts": now,
                "last_token": token,
                "last_error": "missing_symbol_and_name",
                "last_status_code": None,
                "last_result_count": None,
            }
        )
        return None

    query = _build_query(token, symbol, name)
    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    params = {
        "query": query,
        "max_results": max(10, min(int(X_SEARCH_MAX_RESULTS), 100)),
        "tweet.fields": "public_metrics,created_at,author_id",
        "expansions": "author_id",
        "user.fields": "public_metrics,username,verified",
    }
    try:
        _LAST_HEALTH.update({"last_query_ts": time.time(), "last_token": token, "last_error": None})
        r = requests.get(
            "https://api.x.com/2/tweets/search/recent",
            headers=headers,
            params=params,
            timeout=8,
        )
        _LAST_HEALTH["last_status_code"] = r.status_code
        if r.status_code >= 300:
            _LAST_HEALTH.update(
                {
                    "last_error_ts": time.time(),
                    "last_error": f"http_{r.status_code}",
                    "last_result_count": None,
                }
            )
            print(f"[x-signal] status={r.status_code} token={token} body={r.text[:160]}", flush=True)
            return None
        payload = r.json()
        tweets = payload.get("data") or []
        if not isinstance(tweets, list):
            _LAST_HEALTH.update(
                {
                    "last_error_ts": time.time(),
                    "last_error": "invalid_tweets_payload",
                    "last_result_count": None,
                }
            )
            return None
        users_by_id = _user_lookup(payload)
        authors = set()
        heavy_authors = set()
        verified_authors = set()
        likes = 0
        retweets = 0
        replies = 0
        followers = 0
        for tweet in tweets:
            if not isinstance(tweet, dict):
                continue
            author_id = tweet.get("author_id")
            if isinstance(author_id, str) and author_id:
                authors.add(author_id)
                user = users_by_id.get(author_id) or {}
                username = str(user.get("username") or "").strip().lower()
                if author_id in X_HEAVY_AUTHOR_IDS or username in X_HEAVY_HANDLES:
                    heavy_authors.add(author_id)
                if user.get("verified") is True:
                    verified_authors.add(author_id)
                public = user.get("public_metrics") if isinstance(user.get("public_metrics"), dict) else {}
                followers += int(public.get("followers_count") or 0)
            metrics = tweet.get("public_metrics") or {}
            likes += int(metrics.get("like_count") or 0)
            retweets += int(metrics.get("retweet_count") or 0)
            replies += int(metrics.get("reply_count") or 0)
        result = {
            "query": query,
            "tweet_count": len(tweets),
            "unique_authors": len(authors),
            "heavy_author_count": len(heavy_authors),
            "verified_author_count": len(verified_authors),
            "author_followers": followers,
            "likes": likes,
            "retweets": retweets,
            "replies": replies,
        }
        _CACHE[token] = (now, result)
        _LAST_HEALTH.update(
            {
                "last_success_ts": time.time(),
                "last_error": None,
                "last_result_count": result["tweet_count"],
                "cache_size": len(_CACHE),
            }
        )
        print(
            f"[x-signal] token={token} tweets={result['tweet_count']} authors={result['unique_authors']} likes={likes}",
            flush=True,
        )
        return result
    except Exception as ex:
        _LAST_HEALTH.update(
            {
                "last_error_ts": time.time(),
                "last_error": f"{type(ex).__name__}: {ex}"[:240],
                "last_result_count": None,
            }
        )
        print(f"[x-signal] exception token={token} error={ex}", flush=True)
        return None

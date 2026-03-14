import time
from typing import Any, Optional

import requests

from worker.config import X_BEARER_TOKEN, X_QUERY_TEMPLATE, X_SEARCH_MAX_RESULTS
from worker.metadata import fetch_token_metadata

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 300


def _build_query(token: str, symbol: str, name: str) -> str:
    query = X_QUERY_TEMPLATE
    return query.format(token=token, symbol=symbol, name=name)


def fetch_x_signal(token: str, symbol: str = "", name: str = "") -> Optional[dict[str, Any]]:
    if not X_BEARER_TOKEN or not token:
        return None
    now = time.time()
    cached = _CACHE.get(token)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return cached[1]

    if not symbol or not name:
        meta = fetch_token_metadata(token)
        if meta:
            symbol = symbol or str(meta.get("symbol") or "").strip()
            name = name or str(meta.get("name") or "").strip()
    if not symbol and not name:
        return None

    query = _build_query(token, symbol, name)
    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    params = {
        "query": query,
        "max_results": max(10, min(int(X_SEARCH_MAX_RESULTS), 100)),
        "tweet.fields": "public_metrics,created_at,author_id",
    }
    try:
        r = requests.get(
            "https://api.x.com/2/tweets/search/recent",
            headers=headers,
            params=params,
            timeout=8,
        )
        if r.status_code >= 300:
            print(f"[x-signal] status={r.status_code} token={token} body={r.text[:160]}", flush=True)
            return None
        payload = r.json()
        tweets = payload.get("data") or []
        if not isinstance(tweets, list):
            return None
        authors = set()
        likes = 0
        retweets = 0
        replies = 0
        for tweet in tweets:
            if not isinstance(tweet, dict):
                continue
            author_id = tweet.get("author_id")
            if isinstance(author_id, str) and author_id:
                authors.add(author_id)
            metrics = tweet.get("public_metrics") or {}
            likes += int(metrics.get("like_count") or 0)
            retweets += int(metrics.get("retweet_count") or 0)
            replies += int(metrics.get("reply_count") or 0)
        result = {
            "query": query,
            "tweet_count": len(tweets),
            "unique_authors": len(authors),
            "likes": likes,
            "retweets": retweets,
            "replies": replies,
        }
        _CACHE[token] = (now, result)
        print(
            f"[x-signal] token={token} tweets={result['tweet_count']} authors={result['unique_authors']} likes={likes}",
            flush=True,
        )
        return result
    except Exception as ex:
        print(f"[x-signal] exception token={token} error={ex}", flush=True)
        return None

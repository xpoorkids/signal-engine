import json
import os
import time
from pathlib import Path
from typing import Any

import requests

J7TRACKER_DEFAULT_TIMEOUT_SEC = 10

LAST_J7TRACKER_HEALTH: dict[str, Any] = {
    "enabled": False,
    "configured": False,
    "last_finished_ts": None,
    "token_count": 0,
    "last_error": None,
}


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_token_address(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if "/coin/" in token:
        token = token.rsplit("/coin/", 1)[-1]
    if "/t/" in token:
        token = token.rsplit("/t/", 1)[-1]
    if "/meme/" in token:
        token = token.rsplit("/meme/", 1)[-1]
    token = token.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    if not token or "/" in token:
        return ""
    if len(token) < 32 or len(token) > 64:
        return ""
    return token


def _add_token(tokens: list[str], seen: set[str], value: object) -> None:
    token = _normalize_token_address(value)
    if token and token not in seen:
        seen.add(token)
        tokens.append(token)


def _extract_tokens_from_json(data: Any) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    token_keys = {
        "address",
        "tokenaddress",
        "token_address",
        "mint",
        "mintaddress",
        "contractaddress",
        "contract_address",
        "ca",
        "token",
        "url",
    }

    def walk(value: Any, *, in_list: bool = False) -> None:
        if isinstance(value, dict):
            chain = str(value.get("chain") or value.get("chainId") or value.get("network") or "").lower()
            if chain and chain not in {"sol", "solana"}:
                return
            for key, child in value.items():
                lowered = str(key).lower()
                if lowered in token_keys and not isinstance(child, (dict, list)):
                    _add_token(tokens, seen, child)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child, in_list=True)
        elif in_list and isinstance(value, str):
            _add_token(tokens, seen, value)

    walk(data)
    return tokens


def _tokens_from_text(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        return _extract_tokens_from_json(json.loads(raw))
    except Exception:
        tokens: list[str] = []
        seen: set[str] = set()
        for item in raw.replace("\n", ",").split(","):
            _add_token(tokens, seen, item)
        return tokens


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    session_id = os.getenv("SIGNAL_ENGINE_J7_SESSION_ID", "").strip()
    api_key = os.getenv("SIGNAL_ENGINE_J7_API_KEY", "").strip()
    if session_id:
        headers["Authorization"] = f"Bearer {session_id}"
        headers["x-session-id"] = session_id
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _fetch_feed_tokens(url: str) -> list[str]:
    timeout = float(os.getenv("SIGNAL_ENGINE_J7_TIMEOUT_SEC", str(J7TRACKER_DEFAULT_TIMEOUT_SEC)) or J7TRACKER_DEFAULT_TIMEOUT_SEC)
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return _extract_tokens_from_json(response.json())


def get_j7tracker_health() -> dict[str, Any]:
    return dict(LAST_J7TRACKER_HEALTH)


def fetch_j7tracker_tokens() -> list[str]:
    enabled = _truthy_env("SIGNAL_ENGINE_J7_ENABLED")
    feed_url = os.getenv("SIGNAL_ENGINE_J7_FEED_URL", "").strip()
    inline_tokens = os.getenv("SIGNAL_ENGINE_J7_TOKENS", "").strip()
    inline_export = os.getenv("SIGNAL_ENGINE_J7_EXPORT_JSON", "").strip()
    export_path = os.getenv("SIGNAL_ENGINE_J7_EXPORT_PATH", "").strip()
    configured = bool(feed_url or inline_tokens or inline_export or export_path)
    LAST_J7TRACKER_HEALTH.update(
        {
            "enabled": enabled,
            "configured": configured,
            "last_finished_ts": time.time(),
            "token_count": 0,
            "last_error": None,
        }
    )
    if not enabled or not configured:
        return []

    tokens: list[str] = []
    seen: set[str] = set()

    def extend(items: list[str]) -> None:
        for item in items:
            _add_token(tokens, seen, item)

    try:
        if feed_url:
            extend(_fetch_feed_tokens(feed_url))
        if inline_tokens:
            extend(_tokens_from_text(inline_tokens))
        if inline_export:
            extend(_tokens_from_text(inline_export))
        if export_path:
            raw = Path(export_path).read_text(encoding="utf-8")
            extend(_tokens_from_text(raw))
        LAST_J7TRACKER_HEALTH["token_count"] = len(tokens)
        return tokens
    except Exception as exc:
        LAST_J7TRACKER_HEALTH.update(
            {
                "last_error": f"{type(exc).__name__}: {exc}"[:240],
                "token_count": len(tokens),
            }
        )
        raise

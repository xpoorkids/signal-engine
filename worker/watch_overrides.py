from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _enabled() -> bool:
    return os.getenv("SIGNAL_ENGINE_ENABLE_WATCH_OVERRIDE_CONSUMPTION", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _cache_ttl_seconds() -> int:
    try:
        return max(5, int(os.getenv("SIGNAL_ENGINE_WATCH_OVERRIDE_CACHE_TTL_SECONDS", "30") or "30"))
    except (TypeError, ValueError):
        return 30


def _public_base_url() -> str:
    return (
        os.getenv("SIGNAL_ENGINE_LEARNING_WRITE_BASE_URL", "").strip().rstrip("/")
        or os.getenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    )


def _remote_override(token: str) -> dict[str, Any] | None:
    base_url = _public_base_url()
    if not base_url:
        return None
    url = f"{base_url}/learning/ops/watch-overrides/{token}/active"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8") or "{}")
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _local_override(token: str) -> dict[str, Any] | None:
    try:
        from app.services.signal_learning_service import get_active_watch_override

        return get_active_watch_override(token)
    except Exception:
        return None


def _get_override(token: str) -> dict[str, Any] | None:
    now = time.time()
    cached = _CACHE.get(token)
    if cached and now - cached[0] <= _cache_ttl_seconds():
        return cached[1]
    override = _remote_override(token) or _local_override(token)
    _CACHE[token] = (now, override)
    return override


def resolve_consumable_watch_override(
    token: str | None,
    *,
    market_cap_usd: float | None,
    liquidity_usd: float | None,
) -> dict[str, Any] | None:
    if not _enabled():
        return None
    target = str(token or "").strip()
    if not target:
        return None
    override = _get_override(target)
    if not override:
        return None
    try:
        target_market_cap = float(override.get("target_market_cap_usd") or 100000.0)
        min_liquidity = float(override.get("min_liquidity_usd") or 15000.0)
    except (TypeError, ValueError):
        target_market_cap = 100000.0
        min_liquidity = 15000.0
    mc = float(market_cap_usd or 0.0)
    liq = float(liquidity_usd or 0.0)
    checks = {
        "market_cap_usd": mc,
        "liquidity_usd": liq,
        "target_market_cap_usd": target_market_cap,
        "min_liquidity_usd": min_liquidity,
        "market_cap_ok": mc >= target_market_cap,
        "liquidity_ok": liq >= min_liquidity,
    }
    if not (checks["market_cap_ok"] and checks["liquidity_ok"]):
        return None
    return {**override, "checks": checks, "consumable": True}

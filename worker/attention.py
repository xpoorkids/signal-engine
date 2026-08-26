"""
Attention scoring for token-level early signal strength.

Purpose
-------
- Computes the attention score consumed by `worker.promote` for candidate,
  heating-up, and promotion decisions.
- Aggregates local buyer activity, DexScreener/Birdeye/PumpPortal signals,
  tracked-wallet overlap, narrative hits, and optional X/Twitter momentum into
  one bounded score plus human-readable reasons and raw metrics.
- This file does not decide alert routing on its own; it provides the attention
  evidence that later gates and promotion rules act on.

Runtime data flow
-----------------
Inputs:
- A normalized `Event` with token and optional metadata in `extra`.
- Runtime state from `EngineState` and token state (`worker.token_state`),
  especially recent buyer/burst history.
- External APIs:
  - DexScreener orders/boosts
  - Birdeye trending
  - PumpPortal websocket trade stream
  - X signal fetcher when the token has enough other evidence to justify the
    call

Transformations:
1. Read local buyer breadth and burst metrics from state.
2. Add optional external attention features if enabled and available.
3. Add tracked-wallet, KOL, and narrative signals.
4. Conditionally query X only when earlier evidence suggests the call is worth
   the latency/cost.
5. Apply acceleration boost and anti-wash suppression.
6. Clamp the final score into `[0.0, 1.0]`.

Outputs:
- `compute_attention()` returns:
  - `attention_score`
  - `reasons`
  - `metrics`
- `register_buyer()` updates short-lived token-local rolling windows used by
  later attention computations.

Key logic
---------
- Local breadth and burst are the base of the score:
  - 5m unique buyers
  - 15m unique buyers
  - 60s burst count
- `register_buyer()` also updates anti-wash and acceleration state:
  - concentrated repeat buying in 30s can suppress the final score
  - rapid unique-buyer growth in 10s adds a boost
- DexScreener boosts contribute when orders/boosts are present.
- Birdeye and PumpPortal are optional enrichment paths.
- X is intentionally gated by `should_query_x`; this avoids calling X for every
  token and keeps the hot path cheaper.
- Missing external sources are surfaced in `reasons` as
  `source_unavailable:*`; they do not by themselves force the score to zero.

Failure modes
-------------
- External source timeout/failure:
  - The score falls back to local/stateful features and adds
    `source_unavailable:*` reasons.
  - This can lower confidence without fully suppressing a valid token.
- Missing buyer state:
  - If upstream ingestion never registered buyers, local breadth/burst terms
    stay at zero and the token may fail candidate or sniper thresholds.
- Wash-like traffic:
  - Heavy concentration from one wallet can reduce the score via the
    anti-wash multiplier.
- PumpPortal tracker task not running:
  - PumpPortal contribution remains zero even if enabled.

Logging and observability
-------------------------
Primary trace points:
- `[burst-weight]`
- `[anti-wash]`
- `[acceleration]`
- `[attention-components]`

How to debug an unexpectedly weak attention score:
1. Confirm buyers are being registered with `[burst-weight]`.
2. Inspect `[anti-wash]` and `[acceleration]` for suppression/boost effects.
3. Check whether `source_unavailable:*` reasons were added.
4. Compare the returned metrics in `Event.extra["metrics"]` downstream in
   `worker.promote`.

Dependencies and config
-----------------------
Internal dependencies:
- `worker.token_state`
- `worker.x_signal`
- `app.services.state_service`

External dependencies:
- DexScreener HTTP API
- Birdeye HTTP API
- PumpPortal websocket API

Important config inputs:
- `ENABLE_PUMPORTAL`
- `ENABLE_BIRDEYE`
- `ENABLE_X_SIGNAL`
- `BIRDEYE_API_KEY`
- `TRACKED_SMART_WALLETS`
- `KOL_WALLETS`
- `NARRATIVE_KEYWORDS`

Gotchas
-------
- Attention is intentionally composite and non-linear; a token can have strong
  local flow but still miss attention thresholds if breadth is narrow or wash
  suppression applies.
- `register_buyer()` and `compute_attention()` are coupled through shared
  rolling token state. If ingestion misses buyer registration, the score will
  understate real market activity.
- X enrichment is conditional, so absence of X metrics often means the query
  was never attempted, not that the token had zero discussion.
"""

import asyncio
import json
import re
import time
from collections import deque
from typing import Tuple, List, Dict, Any, Optional
import threading

import requests

from worker.config import (
    ENABLE_PUMPORTAL,
    ENABLE_BIRDEYE,
    ENABLE_X_SIGNAL,
    BIRDEYE_API_KEY,
    TRACKED_SMART_WALLETS,
    KOL_WALLETS,
    NARRATIVE_KEYWORDS,
    VIRAL_THEME_KEYWORDS,
)
from worker.signal_policy import attention_scoring_policy, candidate_signal_policy
from worker.token_state import _ts
from app.services.state_service import get_dynamic_tracked_wallets, get_dynamic_kol_wallets
from worker.x_signal import fetch_x_signal

DEXSCREENER_ORDERS_URL = "https://api.dexscreener.com/orders/v1/solana/{token}"
DEX_CACHE_TTL_SEC = 30

_DEX_CACHE: Dict[str, tuple[float, Any]] = {}


def _append_reason(reasons: list[str], text: str) -> None:
    if text and text not in reasons:
        reasons.append(text)


def _metric_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _recent_buyers(state: Any, token: str, window_sec: int = 300) -> set[str]:
    if not token:
        return set()
    try:
        trades = getattr(state, "_buyer_trades", {}).get(token, [])
    except Exception:
        trades = []
    if not trades:
        return set()
    cutoff = time.time() - window_sec
    return {buyer for ts, buyer, _ in trades if ts >= cutoff}


def _narrative_hits(e: Any) -> list[str]:
    if not NARRATIVE_KEYWORDS:
        return []
    extra = getattr(e, "extra", {}) if hasattr(e, "extra") else {}
    symbol = str((extra or {}).get("symbol") or "").lower()
    name = str((extra or {}).get("name") or "").lower()
    haystack = f"{symbol} {name}".strip()
    if not haystack:
        return []
    return [kw for kw in NARRATIVE_KEYWORDS if kw in haystack]


def _viral_theme_hits(e: Any) -> tuple[list[str], list[str]]:
    extra = getattr(e, "extra", {}) if hasattr(e, "extra") else {}
    symbol = str((extra or {}).get("symbol") or "").lower()
    name = str((extra or {}).get("name") or "").lower()
    candidate = (extra or {}).get("dex_scan_candidate") if isinstance(extra, dict) else {}
    if isinstance(candidate, dict):
        symbol = symbol or str(candidate.get("symbol") or "").lower()
        name = name or str(candidate.get("name") or "").lower()
    haystack = f"{symbol} {name}".strip()
    if not haystack:
        return [], []
    words = set(re.findall(r"[a-z0-9]+", haystack))
    compact = re.sub(r"[^a-z0-9]+", "", haystack)
    hits: list[str] = []
    for keyword in VIRAL_THEME_KEYWORDS:
        key = str(keyword or "").strip().lower()
        key_compact = re.sub(r"[^a-z0-9]+", "", key)
        if not key_compact:
            continue
        if key_compact in words or (len(key_compact) >= 4 and key_compact in compact):
            hits.append(key_compact)
    category_map = {
        "animal": {
            "dog",
            "cat",
            "frog",
            "pepe",
            "shark",
            "whale",
            "bull",
            "bear",
            "wolf",
            "ape",
            "monkey",
            "goat",
            "duck",
            "bird",
            "chicken",
            "penguin",
            "hamster",
            "capybara",
            "fox",
            "tiger",
            "lion",
        },
        "event": {
            "election",
            "debate",
            "olympics",
            "worldcup",
            "superbowl",
            "finals",
            "ufc",
            "fight",
            "fed",
            "fomc",
            "cpi",
            "ratecut",
            "launch",
            "eclipse",
            "halloween",
            "christmas",
        },
        "viral": {"tiktok", "viral", "trend", "meta", "meme"},
    }
    categories = [
        category
        for category, members in category_map.items()
        if any(hit in members for hit in hits)
    ]
    return list(dict.fromkeys(hits)), categories




def burst_weight_from_sol(sol: float) -> int:
    policy = attention_scoring_policy()
    if sol < policy.burst_weight_small_buy_sol:
        return policy.burst_weight_small_value
    if sol < policy.burst_weight_medium_buy_sol:
        return policy.burst_weight_medium_value
    if sol < policy.burst_weight_large_buy_sol:
        return policy.burst_weight_large_value
    return policy.burst_weight_extreme_value




def register_buyer(mint: str, buyer: str, sol_spent: float | None = None) -> int:
    """
    Update short-window buyer state used by attention, acceleration, and
    anti-wash scoring.
    """
    if not mint or not buyer:
        return 1
    sol_val = float(sol_spent or 0.0)
    weight = burst_weight_from_sol(sol_val)
    print(f"[burst-weight] token={mint} sol={sol_val} weight={weight}", flush=True)

    now = time.time()
    st = _ts(mint)

    st.buyers_10s.append((buyer, now))
    while st.buyers_10s and now - st.buyers_10s[0][1] > 10:
        st.buyers_10s.popleft()
    unique_10s = len({b for b, _ in st.buyers_10s})

    st.burst_10s.append((now, weight))
    while st.burst_10s and now - st.burst_10s[0][0] > 10:
        st.burst_10s.popleft()
    burst10s = sum(x[1] for x in st.burst_10s)

    st.buys_30s.append((buyer, now, weight, sol_val))
    while st.buys_30s and now - st.buys_30s[0][1] > 30:
        st.buys_30s.popleft()

    counts: Dict[str, int] = {}
    for b, _, _, _ in st.buys_30s:
        counts[b] = counts.get(b, 0) + 1
    total = len(st.buys_30s)
    unique_30s = len(counts)
    top_share = (max(counts.values()) / total) if total else 0.0
    wash_policy = candidate_signal_policy()
    score_policy = attention_scoring_policy()
    wash_suppress = score_policy.anti_wash_penalty if (
        top_share >= wash_policy.anti_wash_top_wallet_share
        and unique_30s <= wash_policy.anti_wash_unique_wallets_30s
    ) else 0.0

    if wash_suppress:
        print(
            f"[anti-wash] token={mint} top_share={top_share:.2f} unique_30s={unique_30s} suppress={wash_suppress}",
            flush=True,
        )

    accel_boost = 0.0
    if unique_10s == score_policy.acceleration_unique_3_min:
        accel_boost = score_policy.acceleration_unique_3_boost
    elif unique_10s == score_policy.acceleration_unique_4_min:
        accel_boost = score_policy.acceleration_unique_4_boost
    elif unique_10s >= score_policy.acceleration_unique_5_min:
        accel_boost = score_policy.acceleration_unique_5_boost
    print(f"[acceleration] token={mint} unique_10s={unique_10s} boost={accel_boost}", flush=True)

    return weight




def _acceleration_boost(mint: str) -> float:
    if not mint:
        return 0.0
    now = time.time()
    st = _ts(mint)
    policy = attention_scoring_policy()
    while st.buyers_10s and now - st.buyers_10s[0][1] > 10:
        st.buyers_10s.popleft()
    unique_10s = len(set(b for b, _ in st.buyers_10s))
    boost = 0.0
    if unique_10s == policy.acceleration_unique_3_min:
        boost = policy.acceleration_unique_3_boost
    elif unique_10s == policy.acceleration_unique_4_min:
        boost = policy.acceleration_unique_4_boost
    elif unique_10s >= policy.acceleration_unique_5_min:
        boost = policy.acceleration_unique_5_boost
    return boost




def _anti_wash_multiplier(mint: str) -> float:
    if not mint:
        return 1.0
    now = time.time()
    st = _ts(mint)
    while st.buys_30s and now - st.buys_30s[0][1] > 30:
        st.buys_30s.popleft()
    if not st.buys_30s:
        return 1.0
    total = len(st.buys_30s)
    by_wallet: Dict[str, int] = {}
    for buyer, _, _, _ in st.buys_30s:
        by_wallet[buyer] = by_wallet.get(buyer, 0) + 1
    unique_wallets = len(by_wallet)
    top_wallet_share = max(by_wallet.values()) / total if total else 0.0
    wash_policy = candidate_signal_policy()
    score_policy = attention_scoring_policy()
    if (
        top_wallet_share >= wash_policy.anti_wash_top_wallet_share
        and unique_wallets <= wash_policy.anti_wash_unique_wallets_30s
    ):
        return score_policy.anti_wash_multiplier
    return 1.0


class _PumpPortalTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tracked_tokens: set[str] = set()
        self._trades: Dict[str, deque[float]] = {}
        self._task_started = False
        self._connected = False
        self._last_error: Optional[str] = None

    def ensure_started(self) -> None:
        if self._task_started:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task_started = True
        loop.create_task(self._run())

    def track_token(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._tracked_tokens.add(token)
            if token not in self._trades:
                self._trades[token] = deque()

    def trade_burst(self, token: str, window_sec: int = 60) -> int:
        if not token:
            return 0
        now = time.time()
        with self._lock:
            trades = self._trades.get(token)
            if not trades:
                return 0
            while trades and now - trades[0] > window_sec:
                trades.popleft()
            return len(trades)

    async def _run(self) -> None:
        try:
            import websockets  # type: ignore
        except Exception as ex:
            self._last_error = f"websockets_import:{ex}"
            return

        uri = "wss://pumpportal.fun/api/data"
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                    self._connected = True
                    self._last_error = None
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    sent_tokens: set[str] = set()
                    while True:
                        with self._lock:
                            pending = list(self._tracked_tokens - sent_tokens)
                        for token in pending:
                            try:
                                await ws.send(
                                    json.dumps(
                                        {
                                            "method": "subscribeTokenTrade",
                                            "keys": [token],
                                        }
                                    )
                                )
                                sent_tokens.add(token)
                            except Exception:
                                pass

                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue

                        token = _extract_token(msg)
                        if token:
                            with self._lock:
                                if token not in self._trades:
                                    self._trades[token] = deque()
                                self._trades[token].append(time.time())
            except Exception as ex:
                self._connected = False
                self._last_error = str(ex)
                await asyncio.sleep(5)


_PUMPPORTAL = _PumpPortalTracker()


def _extract_token(msg: Any) -> Optional[str]:
    try:
        data = json.loads(msg)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("token", "tokenAddress", "mint", "address"):
        if key in data and isinstance(data[key], str):
            return data[key]
    return None


def _dexscreener_boosts_count(token: str) -> Optional[int]:
    if not token:
        return None
    now = time.time()
    cached = _DEX_CACHE.get(token)
    if cached and now - cached[0] < DEX_CACHE_TTL_SEC:
        return cached[1]
    try:
        r = requests.get(DEXSCREENER_ORDERS_URL.format(token=token), timeout=6)
        if r.status_code >= 300:
            return None
        data = r.json()
        if isinstance(data, list):
            count = len(data)
        elif isinstance(data, dict):
            if "orders" in data and isinstance(data["orders"], list):
                count = len(data["orders"])
            else:
                count = len(data.get("data", [])) if isinstance(data.get("data"), list) else 0
        else:
            count = 0
        _DEX_CACHE[token] = (now, count)
        return count
    except Exception:
        return None


def _birdeye_trending(token: str) -> Optional[Dict[str, Any]]:
    if not BIRDEYE_API_KEY or not token:
        return None
    try:
        r = requests.get(
            "https://public-api.birdeye.so/defi/trending",
            headers={"X-API-KEY": BIRDEYE_API_KEY},
            params={"chain": "solana"},
            timeout=6,
        )
        if r.status_code >= 300:
            return None
        data = r.json()
        items = data.get("data", [])
        if not isinstance(items, list):
            return None
        for idx, item in enumerate(items):
            addr = item.get("address") or item.get("tokenAddress") or item.get("mint")
            if addr == token:
                return {"rank": idx + 1, "entry": item}
        return {"rank": None}
    except Exception:
        return None


def compute_attention(e, state) -> Tuple[float, List[str], Dict[str, Any]]:
    """
    Compute the token's composite attention score for downstream routing.

    Returns:
      attention_score: float in [0, 1]
      reasons: human-readable list of strings
      metrics: dictionary of raw numbers
    """
    attention_score = 0.0
    reasons: List[str] = []
    metrics: Dict[str, Any] = {
        "burst_count_60s": 0,
        "unique_buyers_5m": 0,
        "unique_buyers_15m": 0,
        "dexscreener_boosts_count": 0,
        "pumportal_trade_burst": 0,
        "birdeye_trending": False,
        "tracked_wallet_hits": 0,
        "kol_wallet_hits": 0,
        "narrative_hits": [],
        "viral_theme_hits": [],
        "viral_theme_categories": [],
        "viral_x_signal": False,
        "x_tweet_count": 0,
        "x_unique_authors": 0,
        "x_heavy_author_count": 0,
        "x_verified_author_count": 0,
        "x_author_followers": 0,
        "x_likes": 0,
        "unique_wallets_30s": 0,
        "top_wallet_share_30s": 0.0,
        "discovery_sources": [],
        "community_takeover": False,
        "j7tracker_watch": False,
        "external_seed_watch": False,
        "non_x_discovery_support": False,
        "paid_visibility": False,
        "paid_visibility_class": "unknown",
        "source_stability": "unknown",
        "independent_flow_confirmed": False,
        "dex_scan_repeat_count": 0,
        "dex_scan_momentum_slope": 0.0,
        "dex_scan_persistent": False,
        "x_query_attempted": False,
        "x_query_reason": "",
        "x_signal_available": False,
        "dex_source_health": {},
    }

    token = getattr(e, "token", None)
    extra = getattr(e, "extra", {}) if hasattr(e, "extra") else {}
    seed_metrics = extra.get("metrics") if isinstance(extra, dict) and isinstance(extra.get("metrics"), dict) else {}
    if seed_metrics:
        if isinstance(seed_metrics.get("discovery_sources"), list):
            metrics["discovery_sources"] = [str(item) for item in seed_metrics.get("discovery_sources") if str(item or "")]
        metrics["community_takeover"] = bool(seed_metrics.get("community_takeover"))
        metrics["j7tracker_watch"] = bool(seed_metrics.get("j7tracker_watch"))
        metrics["external_seed_watch"] = bool(seed_metrics.get("external_seed_watch"))
        source_set = set(metrics["discovery_sources"])
        metrics["non_x_discovery_support"] = bool(
            seed_metrics.get("non_x_discovery_support")
            or metrics["community_takeover"]
            or metrics["j7tracker_watch"]
            or metrics["external_seed_watch"]
            or {"community_takeover", "j7tracker", "external_seed"} & source_set
        )
        metrics["paid_visibility"] = bool(seed_metrics.get("paid_visibility"))
        metrics["paid_visibility_class"] = str(seed_metrics.get("paid_visibility_class") or "unknown")
        metrics["source_stability"] = str(seed_metrics.get("source_stability") or "unknown")
        metrics["independent_flow_confirmed"] = bool(seed_metrics.get("independent_flow_confirmed"))
        if isinstance(seed_metrics.get("dex_source_health"), dict):
            metrics["dex_source_health"] = seed_metrics.get("dex_source_health") or {}
        for key in (
            "dex_scan_repeat_count",
            "dex_scan_momentum_slope",
            "dex_scan_first_seen_age_seconds",
            "dex_scan_minutes_since_previous",
            "dex_scan_previous_volume_5m",
            "dex_scan_previous_liquidity",
            "dex_scan_volume_delta_5m",
            "dex_scan_liquidity_delta_pct",
            "dormant_revival_watch",
            "realish_chart_continuity",
            "single_scan_chart_spike",
            "buy_sell_ratio_5m",
            "volume_liquidity_ratio_5m",
            "price_change_1h",
            "sells_5m",
            "sell_ratio_5m",
        ):
            if key in seed_metrics:
                metrics[key] = seed_metrics.get(key)
        metrics["dex_scan_persistent"] = bool(seed_metrics.get("dex_scan_persistent"))

    # Local burst metrics (stub; state may implement)
    try:
        metrics["burst_count_60s"] = int(state.burst_count_60s(token))
    except Exception:
        metrics["burst_count_60s"] = 0
    try:
        metrics["unique_buyers_5m"] = int(state.unique_buyers_5m(token))
    except Exception:
        metrics["unique_buyers_5m"] = 0
    try:
        metrics["unique_buyers_15m"] = int(state.unique_buyers_15m(token))
    except Exception:
        metrics["unique_buyers_15m"] = 0

    buyers_5m = metrics["unique_buyers_5m"]
    buyers_15m = metrics["unique_buyers_15m"]
    burst_60s = metrics["burst_count_60s"]
    policy = attention_scoring_policy()
    st = _ts(token) if token else None
    if st is not None:
        now = time.time()
        while st.buys_30s and now - st.buys_30s[0][1] > 30:
            st.buys_30s.popleft()
        if st.buys_30s:
            by_wallet: Dict[str, int] = {}
            for buyer, _, _, _ in st.buys_30s:
                by_wallet[buyer] = by_wallet.get(buyer, 0) + 1
            metrics["unique_wallets_30s"] = len(by_wallet)
            metrics["top_wallet_share_30s"] = (max(by_wallet.values()) / len(st.buys_30s)) if st.buys_30s else 0.0

    local_buyers = min(
        policy.local_buyers_max_score,
        (min(buyers_5m, policy.local_buyers_primary_count) * policy.local_buyers_primary_step)
        + (max(buyers_5m - policy.local_buyers_primary_count, 0) * policy.local_buyers_secondary_step),
    )
    local_burst = min(
        policy.local_burst_max_score,
        (min(burst_60s, policy.local_burst_primary_count) * policy.local_burst_primary_step)
        + (max(burst_60s - policy.local_burst_primary_count, 0) * policy.local_burst_secondary_step),
    )
    local_15m = min(
        policy.local_buyers_15m_max_score,
        min(buyers_15m, policy.local_buyers_15m_cap_count) * policy.local_buyers_15m_step,
    )
    local = local_buyers + local_burst + local_15m
    if buyers_5m >= policy.buyer_breadth_reason_min:
        _append_reason(reasons, f"5m buyer breadth: {buyers_5m}")
    if burst_60s >= policy.burst_reason_min:
        _append_reason(reasons, f"1m burst strength: {burst_60s}")
    if buyers_15m >= policy.buyer_breadth_15m_reason_min:
        _append_reason(reasons, f"15m buyer breadth: {buyers_15m}")
    if metrics.get("independent_flow_confirmed"):
        _append_reason(reasons, "DEX independent flow confirmed")
    if metrics.get("dex_scan_persistent"):
        _append_reason(reasons, f"DEX repeat seen: {metrics.get('dex_scan_repeat_count')}")
    if metrics.get("dormant_revival_watch"):
        _append_reason(reasons, "DEX dormant revival watch")
    if metrics.get("realish_chart_continuity"):
        _append_reason(reasons, "DEX realish chart continuity")
    if metrics.get("j7tracker_watch"):
        _append_reason(reasons, "J7Tracker discovery support")
    if metrics.get("external_seed_watch"):
        _append_reason(reasons, "External seed discovery support")

    # DexScreener boosts/orders
    dex_boost = 0.0
    boosts = _dexscreener_boosts_count(token) if token else None
    if boosts is None:
        _append_reason(reasons, "source_unavailable:dexscreener")
    else:
        metrics["dexscreener_boosts_count"] = boosts
        if boosts >= policy.dexscreener_boost_threshold:
            dex_boost = policy.dexscreener_boost_score
            _append_reason(reasons, f"DexScreener boost activity: {boosts}")

    # Birdeye trending (optional)
    birdeye_score = 0.0
    be = _birdeye_trending(token) if token else None
    if be is None:
        if ENABLE_BIRDEYE:
            _append_reason(reasons, "source_unavailable:birdeye")
    else:
        metrics["birdeye_trending"] = be.get("rank") is not None
        metrics["birdeye_rank"] = be.get("rank")
        if be.get("rank") is not None:
            birdeye_score = policy.birdeye_trending_score
            _append_reason(reasons, f"Birdeye trending rank: #{be.get('rank')}")

    # PumpPortal trade burst (optional)
    pumpportal_score = 0.0
    if ENABLE_PUMPORTAL:
        _PUMPPORTAL.ensure_started()
        if token:
            _PUMPPORTAL.track_token(token)
            metrics["pumportal_trade_burst"] = _PUMPPORTAL.trade_burst(token, window_sec=60)
            if metrics["pumportal_trade_burst"] >= policy.pumpportal_burst_threshold:
                pumpportal_score = policy.pumpportal_burst_score
                _append_reason(reasons, f"PumpPortal trade burst: {metrics['pumportal_trade_burst']}")
    else:
        if ENABLE_PUMPORTAL:
            _append_reason(reasons, "source_unavailable:pumpportal")

    tracked_score = 0.0
    recent_buyers = _recent_buyers(state, token or "")
    if recent_buyers:
        tracked_wallets = set(TRACKED_SMART_WALLETS) | get_dynamic_tracked_wallets()
        kol_wallets = set(KOL_WALLETS) | get_dynamic_kol_wallets()
        tracked_hits = len(recent_buyers & tracked_wallets)
        kol_hits = len(recent_buyers & kol_wallets)
        metrics["tracked_wallet_hits"] = tracked_hits
        metrics["kol_wallet_hits"] = kol_hits
        if tracked_hits > 0:
            tracked_score += min(policy.tracked_wallet_max_score, tracked_hits * policy.tracked_wallet_step)
            _append_reason(reasons, f"Smart wallet flow: {tracked_hits}")
        if kol_hits > 0:
            tracked_score += min(policy.kol_wallet_max_score, kol_hits * policy.kol_wallet_step)
            _append_reason(reasons, f"KOL wallet flow: {kol_hits}")

    narrative_score = 0.0
    hits = _narrative_hits(e)
    if hits:
        metrics["narrative_hits"] = hits[:3]
        narrative_score = min(policy.narrative_max_score, len(hits) * policy.narrative_step)
        _append_reason(reasons, f"Narrative alignment: {', '.join(hits[:2])}")

    viral_score = 0.0
    viral_hits, viral_categories = _viral_theme_hits(e)
    if viral_hits:
        metrics["viral_theme_hits"] = viral_hits[:5]
        metrics["viral_theme_categories"] = viral_categories[:3]
        _append_reason(reasons, f"Viral theme watch: {', '.join(viral_hits[:2])}")

    x_score = 0.0
    x_query_reason = ""
    if tracked_score > 0.0:
        x_query_reason = "tracked_wallet"
    elif dex_boost > 0.0 and local >= policy.x_local_gate_with_boost:
        x_query_reason = "dex_boost_with_local_flow"
    elif metrics.get("community_takeover") or metrics.get("paid_visibility"):
        x_query_reason = "curated_or_paid_discovery"
    elif metrics.get("independent_flow_confirmed") or metrics.get("dex_scan_persistent"):
        x_query_reason = "dex_flow_or_repeat_seen"
    elif viral_hits and seed_metrics and (
        _metric_float(seed_metrics.get("volume_5m") or seed_metrics.get("volume_m5")) >= 1_000.0
        or _metric_float(seed_metrics.get("price_change_5m") or seed_metrics.get("price_change_m5")) >= 0.0
    ):
        x_query_reason = "viral_theme_with_dex_interest"
    elif narrative_score > 0.0 and local >= policy.x_local_gate_with_boost:
        x_query_reason = "narrative_with_local_flow"
    elif birdeye_score > 0.0 and local >= policy.x_local_gate_with_birdeye:
        x_query_reason = "birdeye_with_local_flow"
    elif pumpportal_score > 0.0:
        x_query_reason = "pumpportal_flow"
    elif (
        local >= policy.x_local_gate_strong
        and (
            buyers_5m >= policy.x_query_min_buyers_5m
            or burst_60s >= policy.x_query_min_burst_60s
        )
    ):
        x_query_reason = "strong_local_flow"
    should_query_x = bool(ENABLE_X_SIGNAL and token and x_query_reason)
    metrics["x_query_attempted"] = should_query_x
    metrics["x_query_reason"] = x_query_reason if should_query_x else ("disabled_or_no_trigger" if not ENABLE_X_SIGNAL else "no_trigger")
    if should_query_x:
        extra = getattr(e, "extra", {}) if hasattr(e, "extra") else {}
        x_data = fetch_x_signal(
            token,
            str((extra or {}).get("symbol") or ""),
            str((extra or {}).get("name") or ""),
        )
        if x_data:
            metrics["x_signal_available"] = True
            metrics["x_tweet_count"] = int(x_data.get("tweet_count") or 0)
            metrics["x_unique_authors"] = int(x_data.get("unique_authors") or 0)
            metrics["x_heavy_author_count"] = int(x_data.get("heavy_author_count") or 0)
            metrics["x_verified_author_count"] = int(x_data.get("verified_author_count") or 0)
            metrics["x_author_followers"] = int(x_data.get("author_followers") or 0)
            metrics["x_likes"] = int(x_data.get("likes") or 0)
            if metrics["x_tweet_count"] >= policy.x_mentions_threshold:
                x_score += policy.x_mentions_score
            if metrics["x_unique_authors"] >= policy.x_authors_threshold:
                x_score += policy.x_authors_score
            if metrics["x_likes"] >= policy.x_likes_threshold:
                x_score += policy.x_likes_score
            if metrics["x_heavy_author_count"] > 0:
                x_score += policy.x_authors_score
            if metrics["x_verified_author_count"] >= 2 or metrics["x_author_followers"] >= 50_000:
                x_score += policy.x_likes_score
            if (
                viral_hits
                and metrics["x_tweet_count"] >= 8
                and metrics["x_unique_authors"] >= 4
                and (
                    metrics["x_likes"] >= 30
                    or metrics["x_heavy_author_count"] > 0
                    or metrics["x_verified_author_count"] > 0
                )
            ):
                metrics["viral_x_signal"] = True
                viral_score = min(policy.narrative_max_score, policy.narrative_step * 2)
                _append_reason(
                    reasons,
                    (
                        f"Viral X lift: {metrics['x_tweet_count']} mentions / "
                        f"{metrics['x_unique_authors']} authors"
                    ),
                )
            if x_score > 0:
                _append_reason(
                    reasons,
                    (
                        f"X momentum: {metrics['x_tweet_count']} mentions / "
                        f"{metrics['x_unique_authors']} authors / "
                        f"{metrics['x_heavy_author_count']} heavy"
                    ),
                )
        else:
            _append_reason(reasons, "source_unavailable:x")

    attention_score = local + dex_boost + birdeye_score + pumpportal_score + tracked_score + narrative_score + viral_score + x_score
    attention_score += _acceleration_boost(token or "")
    attention_score *= _anti_wash_multiplier(token or "")
    attention_score = max(0.0, min(attention_score, 1.0))
    print(
        "[attention-components] token=%s local_buyers=%.2f local_burst=%.2f local_15m=%.2f dex_boosts=%s total=%.2f"
        % (
            token,
            local_buyers,
            local_burst,
            local_15m,
            metrics["dexscreener_boosts_count"],
            attention_score,
        ),
        flush=True,
    )
    return attention_score, reasons, metrics

import asyncio
import json
import time
from collections import deque, defaultdict
from typing import Tuple, List, Dict, Any, Optional
import threading

import requests

from worker.config import ENABLE_PUMPORTAL, ENABLE_BIRDEYE, BIRDEYE_API_KEY

DEXSCREENER_ORDERS_URL = "https://api.dexscreener.com/orders/v1/solana/{token}"
DEX_CACHE_TTL_SEC = 30

BURST_COUNT_60S_THRESHOLD = 20
UNIQUE_BUYERS_5M_THRESHOLD = 25
DEXSCREENER_BOOST_THRESHOLD = 1
PUMPORTAL_TRADE_BURST_THRESHOLD = 20

_DEX_CACHE: Dict[str, tuple[float, Any]] = {}
_recent_buyers_10s: Dict[str, deque[tuple[str, float]]] = defaultdict(deque)
_recent_wallet_buys_15s: Dict[str, deque[tuple[str, float]]] = defaultdict(deque)
_recent_buys_30s: Dict[str, deque[tuple[str, float, int, float]]] = defaultdict(deque)


def _compute_burst_weight(sol_spent: float) -> int:
    if sol_spent < 0.2:
        return 1
    if sol_spent < 1:
        return 2
    return 4


def register_buyer(mint: str, buyer: str, sol_spent: float | None = None) -> int:
    if not mint or not buyer:
        return 1
    weight = _compute_burst_weight(float(sol_spent or 0.0))
    print(f"[buy-size] token={mint} sol_spent={float(sol_spent or 0.0)}", flush=True)
    print(f"[burst-weight] token={mint} delta={float(sol_spent or 0.0)} weight={weight}", flush=True)

    now = time.time()
    dq = _recent_wallet_buys_15s[mint]
    dq.append((buyer, now))
    while dq and now - dq[0][1] > 15:
        dq.popleft()
    recent_wallets = [b for b, _ in dq]
    if recent_wallets.count(buyer) >= 2 and len(set(recent_wallets)) == 1:
        weight = max(1, weight - 1)
        print(f"[anti-wash-adjust] token={mint} buyer={buyer} new_weight={weight}", flush=True)

    dq10 = _recent_buyers_10s[mint]
    dq10.append((buyer, now))
    while dq10 and now - dq10[0][1] > 10:
        dq10.popleft()
    unique_10s = len(set(b for b, _ in dq10))
    acceleration_boost = 0.0
    if unique_10s == 3:
        acceleration_boost = 0.10
    elif unique_10s == 4:
        acceleration_boost = 0.15
    elif unique_10s >= 5:
        acceleration_boost = 0.20
    print(f"[acceleration] token={mint} unique_10s={unique_10s} boost={acceleration_boost}", flush=True)

    dq30 = _recent_buys_30s[mint]
    dq30.append((buyer, now, weight, float(sol_spent or 0.0)))
    while dq30 and now - dq30[0][1] > 30:
        dq30.popleft()

    return weight


def _acceleration_boost(mint: str) -> float:
    if not mint:
        return 0.0
    now = time.time()
    dq10 = _recent_buyers_10s[mint]
    while dq10 and now - dq10[0][1] > 10:
        dq10.popleft()
    unique_10s = len(set(b for b, _ in dq10))
    boost = 0.0
    if unique_10s == 3:
        boost = 0.10
    elif unique_10s == 4:
        boost = 0.15
    elif unique_10s >= 5:
        boost = 0.20
    print(f"[acceleration] token={mint} unique_10s={unique_10s} boost={boost}", flush=True)
    return boost


def _anti_wash_multiplier(mint: str) -> float:
    if not mint:
        return 1.0
    now = time.time()
    dq30 = _recent_buys_30s[mint]
    while dq30 and now - dq30[0][1] > 30:
        dq30.popleft()
    if not dq30:
        return 1.0
    total = len(dq30)
    by_wallet: Dict[str, int] = {}
    for buyer, _, _, _ in dq30:
        by_wallet[buyer] = by_wallet.get(buyer, 0) + 1
    unique_wallets = len(by_wallet)
    top_wallet_share = max(by_wallet.values()) / total if total else 0.0
    if top_wallet_share >= 0.70 and unique_wallets <= 2:
        print(
            f"[anti-wash] token={mint} top_wallet_share={top_wallet_share:.2f} unique_wallets={unique_wallets} suppress=0.30",
            flush=True,
        )
        return 0.70
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
    }

    token = getattr(e, "token", None)

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

    local_buyers = min(0.25, metrics["unique_buyers_5m"] * 0.05)
    local_burst = min(0.25, metrics["burst_count_60s"] * 0.03)
    local_15m = min(0.20, metrics["unique_buyers_15m"] * 0.02)
    local = local_buyers + local_burst + local_15m

    # DexScreener boosts/orders
    dex_boost = 0.0
    boosts = _dexscreener_boosts_count(token) if token else None
    if boosts is None:
        reasons.append("source_unavailable:dexscreener")
    else:
        metrics["dexscreener_boosts_count"] = boosts
        if boosts >= DEXSCREENER_BOOST_THRESHOLD:
            dex_boost = 0.20
            reasons.append("dexscreener_boost")

    # Birdeye trending (optional)
    birdeye_score = 0.0
    be = _birdeye_trending(token) if token else None
    if be is None:
        if ENABLE_BIRDEYE:
            reasons.append("source_unavailable:birdeye")
    else:
        metrics["birdeye_trending"] = be.get("rank") is not None
        metrics["birdeye_rank"] = be.get("rank")
        if be.get("rank") is not None:
            birdeye_score = 0.10
            reasons.append("birdeye_trending")

    # PumpPortal trade burst (optional)
    pumpportal_score = 0.0
    if ENABLE_PUMPORTAL:
        _PUMPPORTAL.ensure_started()
        if token:
            _PUMPPORTAL.track_token(token)
            metrics["pumportal_trade_burst"] = _PUMPPORTAL.trade_burst(token, window_sec=60)
            if metrics["pumportal_trade_burst"] >= PUMPORTAL_TRADE_BURST_THRESHOLD:
                pumpportal_score = 0.20
                reasons.append("pumpportal_trade_burst")
    else:
        if ENABLE_PUMPORTAL:
            reasons.append("source_unavailable:pumpportal")

    attention_score = local + dex_boost + birdeye_score + pumpportal_score
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

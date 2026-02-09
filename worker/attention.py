import asyncio
import json
import time
from collections import deque
from typing import Tuple, List, Dict, Any, Optional
import threading

import requests

from worker.config import ENABLE_PUMPORTAL, BIRDEYE_API_KEY

DEXSCREENER_ORDERS_URL = "https://api.dexscreener.com/orders/v1/solana/{token}"
DEX_CACHE_TTL_SEC = 30

BURST_COUNT_60S_THRESHOLD = 20
UNIQUE_BUYERS_5M_THRESHOLD = 25
DEXSCREENER_BOOST_THRESHOLD = 1
PUMPORTAL_TRADE_BURST_THRESHOLD = 20

_DEX_CACHE: Dict[str, tuple[float, Any]] = {}


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

    if metrics["burst_count_60s"] >= BURST_COUNT_60S_THRESHOLD:
        attention_score += 0.35
        reasons.append("burst_count_60s>=20")
    if metrics["unique_buyers_5m"] >= UNIQUE_BUYERS_5M_THRESHOLD:
        attention_score += 0.25
        reasons.append("unique_buyers_5m>=25")

    # DexScreener boosts/orders
    boosts = _dexscreener_boosts_count(token) if token else None
    if boosts is None:
        reasons.append("source_unavailable:dexscreener")
    else:
        metrics["dexscreener_boosts_count"] = boosts
        if boosts >= DEXSCREENER_BOOST_THRESHOLD:
            attention_score += 0.20
            reasons.append("dexscreener_boost")

    # Birdeye trending (optional)
    be = _birdeye_trending(token) if token else None
    if be is None:
        reasons.append("source_unavailable:birdeye")
    else:
        metrics["birdeye_trending"] = be.get("rank") is not None
        metrics["birdeye_rank"] = be.get("rank")
        if be.get("rank") is not None:
            attention_score += 0.10
            reasons.append("birdeye_trending")

    # PumpPortal trade burst (optional)
    if ENABLE_PUMPORTAL:
        _PUMPPORTAL.ensure_started()
        if token:
            _PUMPPORTAL.track_token(token)
            metrics["pumportal_trade_burst"] = _PUMPPORTAL.trade_burst(token, window_sec=60)
            if metrics["pumportal_trade_burst"] >= PUMPORTAL_TRADE_BURST_THRESHOLD:
                attention_score += 0.20
                reasons.append("pumpportal_trade_burst")
    else:
        reasons.append("source_unavailable:pumpportal")

    attention_score = min(attention_score, 1.0)
    return attention_score, reasons, metrics

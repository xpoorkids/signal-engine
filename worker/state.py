import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class TokenState:
    token: str
    first_seen_ts: float
    last_seen_ts: float
    signals: int = 0
    confidence: float = 0.0
    creator: Optional[str] = None
    is_promoted: bool = False
    last_alert_ts: float = 0.0
    last_reason: str = ""
    symbol: Optional[str] = None
    name: Optional[str] = None


@dataclass
class EngineState:
    seen_signatures: Dict[str, float] = field(default_factory=dict)
    tokens: Dict[str, TokenState] = field(default_factory=dict)
    cooldown: Dict[str, float] = field(default_factory=dict)
    _buyer_trades: Dict[str, list[tuple[float, str, int]]] = field(default_factory=dict)

    def wallet_cluster_ratio(self, token: Optional[str]) -> float:
        return 0.0

    def liquidity_stable(self, token: Optional[str], window_sec: int = 1800) -> bool:
        return True

    def top_holder_ratio(self, token: Optional[str]) -> float:
        return 0.0

    def bot_trade_cadence(self, token: Optional[str]) -> bool:
        return False

    def burst_count_60s(self, token: Optional[str]) -> int:
        if not token:
            return 0
        trades = self._buyer_trades.get(token, [])
        if not trades:
            return 0
        now = time.time()
        cutoff = now - 60
        count = sum(weight for ts, _, weight in trades if ts >= cutoff)
        print(f"[attention] burst_count_60s token={token} count={count}", flush=True)
        return count

    def unique_buyers_5m(self, token: Optional[str]) -> int:
        if not token:
            return 0
        trades = self._buyer_trades.get(token, [])
        if not trades:
            return 0
        now = time.time()
        cutoff = now - 300
        buyers = {buyer for ts, buyer, _ in trades if ts >= cutoff}
        count = len(buyers)
        print(f"[attention] unique_buyers_5m token={token} count={count}", flush=True)
        return count

    def unique_buyers_15m(self, token: Optional[str]) -> int:
        if not token:
            return 0
        trades = self._buyer_trades.get(token, [])
        if not trades:
            return 0
        now = time.time()
        cutoff = now - 900
        return len({buyer for ts, buyer, _ in trades if ts >= cutoff})

    def has_basic_liquidity(self, token: Optional[str]) -> bool:
        return True

    def record_buyer(
        self,
        token: Optional[str],
        buyer: Optional[str],
        ts: Optional[float] = None,
        weight: int = 1,
    ) -> None:
        if not token or not buyer:
            return
        now = ts if ts is not None else time.time()
        trades = self._buyer_trades.setdefault(token, [])
        seen_buyers = {b for _, b, _ in trades}
        trades.append((now, buyer, int(weight)))
        if buyer not in seen_buyers:
            print(f"[attention] new_buyer token={token} buyer={buyer}", flush=True)
        cutoff = now - 300
        count = len({b for t, b, _ in trades if t >= cutoff})
        print(f"[buyer-count] token={token} unique_buyers_5m={count}", flush=True)
        cutoff = now - 600
        if len(trades) > 1000:
            trades[:] = [(t, b, w) for t, b, w in trades if t >= cutoff]
        else:
            while trades and trades[0][0] < cutoff:
                trades.pop(0)


def ttl_prune(seen: Dict[str, float], ttl_sec: int) -> None:
    now = time.time()
    dead = [k for k, ts in seen.items() if now - ts > ttl_sec]
    for k in dead:
        del seen[k]


def is_sig_new(state: EngineState, sig: Optional[str], ttl_sec: int) -> bool:
    if not sig:
        return True
    ttl_prune(state.seen_signatures, ttl_sec)
    if sig in state.seen_signatures:
        return False
    state.seen_signatures[sig] = time.time()
    return True


def bump_token(
    state: EngineState,
    token: str,
    delta_conf: float,
    reason: str,
    creator: Optional[str] = None,
) -> TokenState:
    now = time.time()
    ts = state.tokens.get(token)
    if not ts:
        ts = TokenState(token=token, first_seen_ts=now, last_seen_ts=now)
        state.tokens[token] = ts
    ts.last_seen_ts = now
    ts.signals += 1
    ts.confidence = max(ts.confidence, 0.0) + delta_conf
    ts.confidence = min(ts.confidence, 0.99)
    if creator and not ts.creator:
        ts.creator = creator
    ts.last_reason = reason
    return ts


def can_alert(state: EngineState, token: str, cooldown_sec: int) -> bool:
    now = time.time()
    last = state.cooldown.get(token, 0.0)
    if now - last < cooldown_sec:
        return False
    state.cooldown[token] = now
    return True

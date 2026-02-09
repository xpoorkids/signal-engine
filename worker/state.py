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


@dataclass
class EngineState:
    seen_signatures: Dict[str, float] = field(default_factory=dict)
    tokens: Dict[str, TokenState] = field(default_factory=dict)
    cooldown: Dict[str, float] = field(default_factory=dict)

    def wallet_cluster_ratio(self, token: Optional[str]) -> float:
        return 0.0

    def liquidity_stable(self, token: Optional[str], window_sec: int = 1800) -> bool:
        return True

    def top_holder_ratio(self, token: Optional[str]) -> float:
        return 0.0

    def bot_trade_cadence(self, token: Optional[str]) -> bool:
        return False

    def burst_count_60s(self, token: Optional[str]) -> int:
        return 0

    def unique_buyers_5m(self, token: Optional[str]) -> int:
        return 0

    def has_basic_liquidity(self, token: Optional[str]) -> bool:
        return True


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

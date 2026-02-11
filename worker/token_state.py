from dataclasses import dataclass, field
from collections import deque, defaultdict
import time


@dataclass
class TokenState:
    buyers_10s: deque = field(default_factory=deque)        # (buyer, ts)
    buys_30s: deque = field(default_factory=deque)          # (buyer, ts, weight, sol)
    burst_10s: deque = field(default_factory=deque)         # (ts, weight)
    age_bypass_until: float = 0.0
    blacklist_until: float = 0.0
    spike_started_at: float = 0.0
    last_unique_buyers: int = 0
    last_burst_weight: int = 0


TOKEN_STATE: dict[str, TokenState] = {}


def _ts(token: str) -> TokenState:
    st = TOKEN_STATE.get(token)
    if not st:
        st = TokenState()
        TOKEN_STATE[token] = st
    return st

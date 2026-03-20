from __future__ import annotations

import time
from dataclasses import dataclass, field
from collections import deque
from typing import Deque, Dict, Optional, Tuple, Any

import requests

from worker.config import HELIUS_API_KEY, HELIUS_RPC_URL
from worker.signal_policy import elite_score_policy


def _now() -> float:
    return time.monotonic()


def _rpc_url() -> str:
    url = (HELIUS_RPC_URL or "").strip()
    api_key = (HELIUS_API_KEY or "").strip()
    if not url:
        return f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    if "api-key=" in url or "apikey=" in url:
        return url
    if api_key:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}api-key={api_key}"
    return url


RPC_URL = _rpc_url()


@dataclass
class EliteTokenState:
    token: str
    last_update_mono: float = 0.0
    age_bypass_until: float = 0.0
    blacklist_until: float = 0.0
    decay_watch_started: float = 0.0
    decay_start_unique_5m: int = 0
    decay_start_burst_10s: int = 0
    decay_start_liq: float = 0.0

    last_attention: float = 0.0
    last_burst_60s: int = 0
    last_unique_buyers_5m: int = 0
    last_liq_usd: float = 0.0
    last_buy_size_sol: float = 0.0

    auth_cache_until: float = 0.0
    mint_authority_active: Optional[bool] = None
    freeze_authority_active: Optional[bool] = None

    liq_cache_until: float = 0.0
    liq_usd: float = 0.0
    liq_locked: Optional[bool] = None
    liq_last: float = 0.0
    liq_last_ts: float = 0.0

    buyers_10s: Deque[Tuple[str, float]] = field(default_factory=deque)
    burst_10s: Deque[Tuple[int, float]] = field(default_factory=deque)
    buys_30s: Deque[Tuple[str, float, int, float]] = field(default_factory=deque)


class EliteTracker:
    def __init__(self) -> None:
        self._state: Dict[str, EliteTokenState] = {}

    def get_state(self, token: str) -> EliteTokenState:
        if token not in self._state:
            self._state[token] = EliteTokenState(token=token)
        return self._state[token]

    def record_buy(self, token: str, buyer: str, weight: int, buy_size_sol: float) -> None:
        now = _now()
        st = self.get_state(token)
        st.last_update_mono = now
        st.last_buy_size_sol = buy_size_sol

        st.buyers_10s.append((buyer, now))
        while st.buyers_10s and now - st.buyers_10s[0][1] > 10:
            st.buyers_10s.popleft()

        st.burst_10s.append((weight, now))
        while st.burst_10s and now - st.burst_10s[0][1] > 10:
            st.burst_10s.popleft()

        st.buys_30s.append((buyer, now, weight, buy_size_sol))
        while st.buys_30s and now - st.buys_30s[0][1] > 30:
            st.buys_30s.popleft()

    def burst_weight_sum_10s(self, token: str) -> int:
        st = self.get_state(token)
        now = _now()
        while st.burst_10s and now - st.burst_10s[0][1] > 10:
            st.burst_10s.popleft()
        return sum(w for w, _ in st.burst_10s)

    def unique_10s(self, token: str) -> int:
        st = self.get_state(token)
        now = _now()
        while st.buyers_10s and now - st.buyers_10s[0][1] > 10:
            st.buyers_10s.popleft()
        return len(set(b for b, _ in st.buyers_10s))

    def distribution_metrics(self, token: str) -> Tuple[int, int, float]:
        st = self.get_state(token)
        now = _now()
        while st.buys_30s and now - st.buys_30s[0][1] > 30:
            st.buys_30s.popleft()
        if not st.buys_30s:
            return 0, 0, 0.0
        total = len(st.buys_30s)
        by_wallet: Dict[str, int] = {}
        for buyer, _, _, _ in st.buys_30s:
            by_wallet[buyer] = by_wallet.get(buyer, 0) + 1
        unique_wallets = len(by_wallet)
        top_wallet_share = max(by_wallet.values()) / total if total else 0.0
        return total, unique_wallets, top_wallet_share

    def auth_check(self, token: str) -> Tuple[bool, bool]:
        st = self.get_state(token)
        now = _now()
        if st.auth_cache_until and now < st.auth_cache_until:
            return bool(st.mint_authority_active), bool(st.freeze_authority_active)
        mint_auth = False
        freeze_auth = False
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    token,
                    {"encoding": "jsonParsed", "commitment": "confirmed"},
                ],
            }
            r = requests.post(RPC_URL, json=payload, timeout=8)
            if r.status_code == 200:
                data = r.json()
                value = (data.get("result") or {}).get("value") or {}
                parsed = (value.get("data") or {}).get("parsed") or {}
                info = parsed.get("info") or {}
                mint_auth = info.get("mintAuthority") is not None
                freeze_auth = info.get("freezeAuthority") is not None
        except Exception:
            pass
        st.mint_authority_active = mint_auth
        st.freeze_authority_active = freeze_auth
        st.auth_cache_until = now + 120
        return mint_auth, freeze_auth

    def liq_check(self, token: str, dex_summary: Optional[Dict[str, Any]]) -> Tuple[float, Optional[bool], bool]:
        st = self.get_state(token)
        now = _now()
        if st.liq_cache_until and now < st.liq_cache_until:
            return st.liq_usd, st.liq_locked, False
        liq_usd = 0.0
        liq_locked = None
        if dex_summary:
            try:
                liq_usd = float(dex_summary.get("liquidity_usd") or 0.0)
            except Exception:
                liq_usd = 0.0
            for key in ("lp_locked", "liquidity_locked", "lpBurned", "lp_burned"):
                if key in dex_summary:
                    liq_locked = bool(dex_summary.get(key))
                    break
        drop_spike = False
        if st.liq_last_ts and now - st.liq_last_ts <= 60 and st.liq_last > 0:
            if liq_usd < st.liq_last * 0.60:
                drop_spike = True
        st.liq_last = liq_usd
        st.liq_last_ts = now
        st.liq_usd = liq_usd
        st.liq_locked = liq_locked
        st.liq_cache_until = now + 60
        return liq_usd, liq_locked, drop_spike

    def compute_elite_score(
        self,
        token: str,
        buy_size_sol: float,
        unique_10s: int,
        total_buys_30s: int,
        unique_wallets_30s: int,
        top_wallet_share: float,
        liq_usd: float,
        liq_locked: Optional[bool],
        hard_fail: bool,
    ) -> int:
        policy = elite_score_policy()
        if hard_fail:
            return policy.hard_fail_score
        capital_score = 1
        if buy_size_sol >= policy.capital_large_buy_sol:
            capital_score = policy.capital_large_score
        elif buy_size_sol >= policy.capital_medium_buy_sol:
            capital_score = policy.capital_medium_score
        elif buy_size_sol >= policy.capital_small_buy_sol:
            capital_score = policy.capital_small_score

        velocity_score = 0
        if unique_10s >= policy.velocity_high_unique_10s:
            velocity_score = policy.velocity_high_score
        elif unique_10s == policy.velocity_mid_unique_10s:
            velocity_score = policy.velocity_mid_score
        elif unique_10s == policy.velocity_low_unique_10s:
            velocity_score = policy.velocity_low_score

        distribution_score = 0
        if (
            top_wallet_share >= policy.concentrated_top_wallet_share
            and unique_wallets_30s <= policy.concentrated_unique_wallets_30s_max
        ):
            distribution_score = policy.concentrated_penalty
        elif unique_wallets_30s >= policy.broad_distribution_unique_wallets_30s_min:
            distribution_score = policy.broad_distribution_bonus

        safety_bonus = 0
        if liq_usd > policy.safety_liquidity_bonus_usd:
            safety_bonus += policy.safety_liquidity_bonus
        if liq_locked is True:
            safety_bonus += policy.safety_locked_bonus

        elite = (capital_score * 2) + (velocity_score * 2) + distribution_score + safety_bonus
        print(
            f"[elite-score] token={token} capital={capital_score} velocity={velocity_score} "
            f"dist={distribution_score} safety={safety_bonus} elite={elite}",
            flush=True,
        )
        return elite

    def update_decay(
        self,
        token: str,
        attention: float,
        burst_10s: int,
        unique_buyers_5m: int,
        liq_usd: float,
    ) -> Optional[int]:
        policy = elite_score_policy()
        st = self.get_state(token)
        now = _now()
        if st.blacklist_until and now < st.blacklist_until:
            return int(st.blacklist_until - now)
        spike = attention >= policy.decay_watch_attention_min
        if spike and st.decay_watch_started == 0:
            st.decay_watch_started = now
            st.decay_start_unique_5m = unique_buyers_5m
            st.decay_start_burst_10s = burst_10s
            st.decay_start_liq = liq_usd
            print(f"[decay-watch] token={token} started=1", flush=True)
            return None
        if st.decay_watch_started and now - st.decay_watch_started <= policy.decay_window_sec:
            no_new_buyers = unique_buyers_5m <= st.decay_start_unique_5m
            burst_drop = burst_10s < (st.decay_start_burst_10s * policy.decay_burst_drop_multiplier)
            liq_drop = liq_usd > 0 and liq_usd < (st.decay_start_liq * policy.decay_liquidity_drop_multiplier)
            if no_new_buyers and (burst_drop or liq_drop):
                st.blacklist_until = now + policy.decay_blacklist_ttl_sec
                print(
                    f"[momentum-fail] token={token} reason=no_follow_through blacklist={policy.decay_blacklist_ttl_sec}",
                    flush=True,
                )
                return policy.decay_blacklist_ttl_sec
        if st.decay_watch_started and now - st.decay_watch_started > policy.decay_window_sec:
            st.decay_watch_started = 0
        return None


ELITE = EliteTracker()

from __future__ import annotations

import asyncio
import time
from typing import Dict, Any, List

from app.services.state_service import (
    get_candidate_state,
    update_candidate_recheck,
    update_candidate_flat_counters,
    should_mute,
)


def _metadata_gate(extra: Dict[str, Any]) -> bool:
    if extra.get("rug_bad") is True:
        return False
    if extra.get("authority_revoked") is False:
        return False
    return True


def min_liquidity_gate(extra: Dict[str, Any], curve_min: float) -> bool:
    dex_summary = extra.get("dex_summary") if isinstance(extra, dict) else None
    if dex_summary:
        return True
    curve_liq = None
    if isinstance(extra.get("bonding_curve_liquidity"), (int, float, str)):
        try:
            curve_liq = float(extra.get("bonding_curve_liquidity") or 0)
        except Exception:
            curve_liq = None
    elif isinstance(extra.get("bonding_curve"), dict):
        try:
            curve_liq = float(extra["bonding_curve"].get("liquidity_usd") or 0)
        except Exception:
            curve_liq = None
    if curve_liq is None:
        return False
    return curve_liq >= curve_min


def _stage_a_delays() -> List[int]:
    return [60, 180, 300, 600, 1200, 1800, 2700, 3600]


def _stage_b_delays() -> List[int]:
    return [7200, 14400, 28800, 43200]


def _stage_c_delays() -> List[int]:
    return [86400, 259200, 604800, 1209600, 2592000]


async def schedule_rechecks(
    state,
    event,
    extra: Dict[str, Any],
    curve_min: float,
    stage: str,
) -> None:
    token = event.token
    if not token:
        return
    if not _metadata_gate(extra):
        return
    if not min_liquidity_gate(extra, curve_min):
        return

    delays = _stage_a_delays() if stage == "A" else _stage_b_delays() if stage == "B" else _stage_c_delays()
    now = int(time.time())
    next_check_at = now + min(delays)
    update_candidate_recheck(token, next_check_at, stage)
    print(f"[recheck-scheduled] token={token} stage={stage} next={next_check_at}", flush=True)

    for delay in delays:
        async def _run_after(d: int) -> None:
            await asyncio.sleep(d)
            if should_mute(token):
                print(f"[recheck-stop] token={token} reason=muted", flush=True)
                return
            ts = state.tokens.get(token)
            if not ts or ts.is_promoted:
                return
            cand = get_candidate_state(token)
            if cand.get("stage") and cand.get("stage") != stage:
                return
            print(f"[recheck-run] token={token} delay={d}", flush=True)
            try:
                await event._recheck_fn(token)
            except Exception:
                print(f"[recheck-run] token={token} delay={d} failed", flush=True)

        asyncio.create_task(_run_after(delay))


def update_stop_counters(
    token: str,
    prev_liq: float,
    curr_liq: float,
    prev_buyers: int,
    curr_buyers: int,
    curve_min: float,
) -> bool:
    cand = get_candidate_state(token)
    flat_liq = int(cand.get("flat_liq_count") or 0)
    flat_buy = int(cand.get("flat_buyer_count") or 0)
    if curr_liq < curve_min:
        flat_liq += 1
    else:
        flat_liq = 0
    if curr_buyers <= prev_buyers:
        flat_buy += 1
    else:
        flat_buy = 0
    update_candidate_flat_counters(token, flat_liq, flat_buy)
    return flat_liq >= 3 or flat_buy >= 3

"""
Critical scoring and routing path for the live Signal Engine.

Purpose
-------
- `process_event()` is the main decision function that turns a raw `Event` into
  zero or more higher-level engine events.
- It sits between ingestion (`worker.helius_listener`, scanner/recheck events)
  and delivery (`worker.runner`).
- This module does not publish to Discord directly. It returns derived
  `Event` objects such as `candidate`, `heating_up`, and `promoted`; the runner
  owns cooldown checks, delivery attempts, and persistence after confirmed
  transport success.

Runtime data flow
-----------------
Input:
- `worker.events.Event` from WS/log ingestion, scanner/recheck, or engine
  scheduled follow-up.
- Common event types that reach this file:
  - `token_resolved`
  - `trade_buy`
  - `recheck`
  - early event types such as `early_logs_initialize_mint` and
    `early_tx_pump_observed`

Transformations:
1. Token state is updated with `bump_token()`.
2. Metadata, creator score, buyer registration, attention, DEX enrichment,
   forensic risk, wallet risk, and elite score are computed.
3. Hard-fail checks run before candidate/promotion logic:
   - mint/freeze authority
   - liquidity/lock/drop checks
   - risk veto
   - momentum decay blacklist
4. Sniper/heating logic runs before candidate emission.
5. Candidate gating runs only if `ENABLE_ATTENTION_CANDIDATE` is enabled.
6. Confidence is recomputed from attention/risk/creator/liquidity and then
   promotion checks run.

Output:
- Returns `list[Event]`.
- Possible derived outputs:
  - original early event only, when token is unresolved
  - `heating_up`
  - `candidate`
  - `promoted`
  - empty list when the signal is blocked or blacklisted

Key routing and precedence rules
--------------------------------
1. Early unresolved events:
- If the event type starts with `early` and `e.token` is missing, the function
  mutates confidence/reasons and returns `[e]`.
- No candidate or promotion path is executed in this branch.

2. Hard-fail path:
- Authority checks and liquidity safety checks can set `hard_fail`.
- If `hard_fail` remains true after liquidity bypass handling, the function
  returns `out` immediately. This blocks candidate, heating_up, and promoted
  outputs.

3. Sniper/heating precedence:
- `sniper_conditions_met` is evaluated before candidate gating.
- When met, a `heating_up` event is appended to `out` immediately with
  `sniper_route` added to reasons.
- Candidate gating still runs afterward. Sniper does not bypass candidate
  evaluation, and candidate failure does not remove the already-appended
  `heating_up` event.
- Delivery still depends on `worker.runner` cooldown and heating quality checks.

4. Candidate behavior:
- Candidate emission is attention-driven and treated as watchlist output, not
  promotion.
- `admission_check_candidate()` decides whether the token is even eligible to
  be considered, based on:
  - age gate / age bypass
  - attention minimum
  - risk veto
  - DEX gate if a pair exists
  - token tradeability verification
  - pump.fun / bonding-curve verification on non-DEX path
- After gate pass, send eligibility is determined separately by
  `_candidate_send_eligible()`, rate limits, and progression state.
- A candidate can be:
  - fully skipped (`candidate_gate_skip`, `candidate_rate_limited`,
    `candidate_not_eligible`)
  - buffered (`candidate_buffered`)
  - emitted (`candidate_ready`)
- `candidate_event_extra` is only created inside the candidate-pass branch, so
  any gate failure prevents a `candidate` event from being returned.

5. Promotion behavior:
- Promotion is evaluated after confidence is recomputed.
- Promotion requires all of the following at runtime:
  - confidence >= policy threshold
  - confirmed DEX pool (`dex_summary` must exist)
  - minimum liquidity
  - minimum 15m buyer count
  - minimum attention
  - risk below promoted max
  - no LP drain
  - no creator sell flag
  - two confirmation passes via `update_promo_confirm()`
  - `evaluate_alert_gate("promoted", dex_summary)` pass
- If any condition fails, promotion returns early with a recorded diagnostic
  decision and no `promoted` event is emitted.

Failure modes and live-debug implications
-----------------------------------------
- Missing token:
  - Early events with no token never reach candidate/promotion logic.
  - Trace via `[promote-skip] reason=token_unresolved`.

- Blacklist / decay:
  - Momentum collapse inside the decay window sets `blacklist_until` and
    returns immediately.
  - Trace via `[momentum-fail]` and later `[blacklist-skip]`.

- Liquidity unknown:
  - Unknown liquidity normally becomes a hard fail unless the balanced-mode
    bypass is satisfied (`unique_10s >= 2`, `burst_10s >= 6`, `elite_score >= 8`).
  - This can silently suppress signals if upstream liquidity enrichment is weak.

- Candidate drop after sniper success:
  - This is expected. `heating_up` can exist without `candidate`.
  - Check `[discord-sniper]` first, then candidate logs to see whether the
    watchlist path was blocked separately.

- Promotion blocked after candidate/heating:
  - Also expected. Promotion has stricter runtime conditions than candidate.
  - Inspect `[promotion-block]`, `[promotion-check]`, and `[gate-skip]`.

- Exceptions in diagnostics:
  - `_record_decision()` is exception-wrapped and logs with
    `[diagnostics] record_signal_decision_failed ...`.
  - Failure here should not stop signal routing, but it reduces observability.

Logging and observability
-------------------------
Primary trace points emitted here:
- `[PROMOTE HANDLER CALLED]` and `[promote-enter]`
- `[token-metadata]`
- `[creator-score]`
- `[skip-attention]`
- `[attention]`, `[attention-metrics]`
- `[execution]`
- `[auth-check]`, `[liq-check]`, `[risk-gate]`
- `[risk-score]`
- `[liq-unknown-bypass]`
- `[age-bypass]`
- `[discord-sniper]` and `[discord-send-attempt]`
- `[decay-watch]`, `[momentum-fail]`, `[blacklist-skip]`
- `[candidate-skip]`, `[candidate-warning]`, `[candidate-lifecycle]`,
  `[candidate-attention]`, `[candidate-progress]`
- `[recheck-stop]`
- `[score-adjust]`, `[exec-bonus]`, `[score-components]`, `[score] computed`
- `[gate-skip] stage=heating_up ...`
- `[promotion-block]`, `[promotion-check]`, `[promotion-validated]`

How to trace one token end-to-end using logs:
1. Start with `[promote-enter] token=...`.
2. Check for early exits:
   `[blacklist-skip]`, `[risk-gate]`, `[momentum-fail]`, candidate skip, or
   promotion block.
3. If sniper behavior is expected, look for `[discord-sniper]`.
4. If candidate behavior is expected, inspect `[candidate-skip]` or
   `[candidate-progress]`.
5. If promotion is expected, inspect `[promotion-check]` and
   `[promotion-validated]`.
6. Then move to `worker.runner` for cooldown, delivery, and persistence.

Dependencies and external inputs
--------------------------------
Internal dependencies:
- `worker.helius_listener`
- `worker.state`, `worker.token_state`
- `worker.attention`
- `worker.dex`
- `worker.forensics`
- `worker.elite`
- `worker.execution`
- `worker.alert_gate`
- `worker.creator_score`
- `worker.recheck`
- `app.services.state_service`
- `app.services.signal_learning_service`

External dependencies:
- Helius metadata via `fetch_token_metadata()`
- DEX data via `dex_enrich_token()`

Important config inputs:
- engine mode and sniper thresholds
- attention candidate toggles and rate limit
- promoted policy thresholds
- risk veto threshold
- decay / blacklist timing
- curve liquidity minimum

Gotchas
-------
- `heating_up` can be appended before confidence is recomputed; the event is
  updated later in-place from final `extra`/confidence before return.
- Candidate decisions are recorded even when no candidate event is returned.
  Use decision logs plus returned events together when debugging.
- Promotion requires a DEX pool. Pump.fun / bonding-curve-only tokens cannot
  become `promoted` in this file.
- `candidate_send` is only advisory here. Actual send suppression still happens
  in `worker.runner`.
- Some imports from `app.services.state_service` are currently used indirectly
  or vestigially; the authoritative runtime behavior is the control flow inside
  `process_event()`, not the import list.
"""

from typing import Dict, Any
import logging
import time
import asyncio
import os
from worker.events import Event
from worker.state import EngineState, bump_token
from worker.config import (
    ENABLE_DEX,
    ENABLE_WALLET,
    ENABLE_FORENSICS,
    ENABLE_ATTENTION,
    ENABLE_EXECUTION,
    ENABLE_RISK_VETO,
    TRADE_VALIDATION_ENABLED,
    ENABLE_ATTENTION_BONUS,
    ENABLE_ATTENTION_CANDIDATE,
    RISK_VETO_THRESHOLD,
    ATTENTION_BONUS_CAP,
    ATTENTION_MIN_FOR_WINDOW,
    ATTENTION_WINDOW_MINUTES,
    ATTENTION_CANDIDATE_THRESHOLD,
    CAND_MIN_CURVE_LIQ_USD,
    EXECUTION_BONUS_CAP,
    MIN_EDGE_BPS,
    ENGINE_MODE,
    MIN_BUY_SOL_FOR_ATTENTION_SNIPER,
    MIN_BUY_SOL_FOR_ATTENTION_LONG,
    LONG_MIN_UNIQUE_BUYERS_5M,
    LONG_MIN_ELITE_SCORE,
    SNIPER_MIN_ELITE_SCORE,
    DISCORD_WEBHOOK_URL,
    DECAY_WINDOW_SECONDS,
    BLACKLIST_SECONDS,
    CREATOR_RISK_WEIGHT,
    EARLY_CREATOR_MIN,
    EARLY_WATCH_RATE_LIMIT_PER_HOUR,
    PROGRESSION_ATTENTION_DELTA,
    PROGRESSION_BUYER_DELTA,
    PROGRESSION_LIQ_DELTA,
    PROGRESSION_SCORE_DELTA,
    PROM_MIN_LIQ_USD,
    PROMOTION_MIN_ATTENTION,
    PROMOTION_MAX_RISK,
    WALLET_DISTRIBUTION_HARD_FAIL_TOP_HOLDER_PCT,
)
from worker.confidence import CONF_WEIGHTS, CAPS, bump
from worker.wallet_risk import score_wallet_risk
from worker.dex import dex_enrich_token, select_best_pair, summarize_pair
from worker.forensics import analyze_risk
from worker.execution import estimate_edge
from worker.expected_value import evaluate_candidate_ev
from worker.trade_validator import validate_trade
from worker.attention import compute_attention, register_buyer
from worker.token_state import _ts
from worker.elite import ELITE
from worker.metadata import fetch_token_metadata
from worker.alert_gate import evaluate_alert_gate, admission_check_candidate
from worker.creator_score import compute_creator_score
from worker.progression import metrics_improved
from worker.recheck import schedule_rechecks, update_stop_counters, min_liquidity_gate
from worker.watch_overrides import resolve_consumable_watch_override
from worker.signal_policy import (
    attention_scoring_policy,
    candidate_lifecycle_policy,
    candidate_send_reasons,
    candidate_signal_policy,
    promotion_confirmation_target,
    classify_route_signal,
    route_signal_policy,
)
from app.services.signal_metrics import compute_confidence_score, metric_state
from app.services.signal_learning_service import classify_policy_regime, record_signal_decision, resolve_live_policy
from app.services.state_service import (
    init as state_init,
    upsert_seen,
    get_last_metrics,
    record_candidate_sent,
    allow_candidate_rate_limit,
    record_creator_deploy,
    get_candidate_state,
    upsert_candidate_state,
    mark_candidate_alert_sent,
    update_candidate_message_id,
    update_promo_confirm,
    should_mute,
)
from app.services.structured_logging import log_event
from app.services.wallet_service import wallet_risk_score

logger = logging.getLogger(__name__)
logger.info("[PROMOTE FILE LOADED]")


def _wallet_distribution_fail_reasons(
    wallet_risk: dict[str, Any] | None,
    *,
    total_buys_30s: int,
    unique_wallets_30s: int,
    top_wallet_share: float,
) -> list[str]:
    reasons: list[str] = []
    payload = wallet_risk if isinstance(wallet_risk, dict) else {}
    wallet_level = str(payload.get("risk") or "").strip().lower()
    try:
        top_holder_pct = float(payload.get("top_holder_pct"))
    except Exception:
        top_holder_pct = None

    if wallet_level == "high":
        if (
            top_holder_pct is not None
            and top_holder_pct >= WALLET_DISTRIBUTION_HARD_FAIL_TOP_HOLDER_PCT
        ):
            reasons.append("wallet_distribution_high_risk")
    if (
        top_holder_pct is not None
        and top_holder_pct >= WALLET_DISTRIBUTION_HARD_FAIL_TOP_HOLDER_PCT
    ):
        reasons.append("wallet_top_holder_concentration")
    if total_buys_30s >= 6 and unique_wallets_30s <= 2 and top_wallet_share >= 0.70:
        reasons.append("bundle_pattern_detected")

    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped


def _wallet_cluster_review(
    wallet_risk: dict[str, Any] | None,
    *,
    total_buys_30s: int,
    unique_wallets_30s: int,
    top_wallet_share: float,
    attention_metrics: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
    risk_score: float | None,
) -> dict[str, Any]:
    payload = wallet_risk if isinstance(wallet_risk, dict) else {}
    metrics = attention_metrics if isinstance(attention_metrics, dict) else {}
    dex = dex_summary if isinstance(dex_summary, dict) else {}
    policy = route_signal_policy()
    candidate_policy = candidate_signal_policy()

    wallet_level = str(payload.get("risk") or "").strip().lower()
    try:
        top_holder_pct = float(payload.get("top_holder_pct"))
    except Exception:
        top_holder_pct = None
    try:
        top10_pct = float(payload.get("top10_pct"))
    except Exception:
        top10_pct = None

    liq = float(dex.get("liquidity_usd") or 0.0)
    buys5m = int(dex.get("txns_m5_buys") or 0)
    sells5m = int(dex.get("txns_m5_sells") or 0)
    vol5m = float(dex.get("volume_m5") or 0.0)
    price_change_m5 = float(dex.get("price_change_m5") or 0.0)
    buyers_5m = int(metrics.get("unique_buyers_5m") or 0)
    burst_60s = int(metrics.get("burst_count_60s") or 0)
    tracked_hits = int(metrics.get("tracked_wallet_hits") or 0)
    kol_hits = int(metrics.get("kol_wallet_hits") or 0)
    smart_wallet_hits = tracked_hits + kol_hits
    risk = float(risk_score) if isinstance(risk_score, (int, float)) else None
    sell_ratio = (float(sells5m) / float(buys5m)) if buys5m > 0 else 0.0
    vol_liq_ratio = (vol5m / liq) if liq > 0.0 else 0.0

    signals: list[str] = []
    blockers: list[str] = []
    score = 0

    if wallet_level == "high":
        blockers.append("wallet_cluster_high_risk")
        score -= 20
    if top_holder_pct is not None and top_holder_pct >= 0.45:
        blockers.append("wallet_cluster_single_holder_dominant")
        score -= 35
    elif top_holder_pct is not None and top_holder_pct >= WALLET_DISTRIBUTION_HARD_FAIL_TOP_HOLDER_PCT:
        blockers.append("wallet_cluster_top_holder_watch")
        score -= 15
    if top10_pct is not None and top10_pct >= 0.70:
        blockers.append("wallet_cluster_top10_dominant")
        score -= 20
    if total_buys_30s >= 6 and unique_wallets_30s <= 2 and top_wallet_share >= 0.70:
        blockers.append("wallet_cluster_bundle_pattern")
        score -= 35
    elif unique_wallets_30s >= 4 and top_wallet_share <= 0.55:
        signals.append("wallet_cluster_local_breadth")
        score += 18
    if buyers_5m >= max(candidate_policy.min_unique_buyers_5m + 2, 5):
        signals.append("wallet_cluster_buyer_breadth")
        score += 18
    if burst_60s >= policy.route_burst_strength_min:
        signals.append("wallet_cluster_burst_confirmed")
        score += 10
    if buys5m >= policy.route_market_support_min_buys5m:
        signals.append("wallet_cluster_market_buys")
        score += 12
    if liq >= policy.heating_min_liq_usd:
        signals.append("wallet_cluster_liquidity_floor")
        score += 12
    if smart_wallet_hits > 0:
        signals.append("wallet_cluster_smart_wallet_support")
        score += 18
    if buys5m > 0 and sell_ratio <= policy.adversarial_max_sell_ratio_5m:
        signals.append("wallet_cluster_sell_pressure_ok")
        score += 8
    elif buys5m > 0:
        blockers.append("wallet_cluster_sell_pressure_high")
        score -= 18
    if liq > 0.0 and vol_liq_ratio > policy.adversarial_max_vol_liq_ratio_5m:
        blockers.append("wallet_cluster_volume_liquidity_spike")
        score -= 16
    if price_change_m5 < -18.0:
        blockers.append("wallet_cluster_dumping")
        score -= 25
    if risk is not None and risk > 0.60:
        blockers.append("wallet_cluster_risk_score_high")
        score -= 18

    toxic_markers = {
        "wallet_cluster_single_holder_dominant",
        "wallet_cluster_top10_dominant",
        "wallet_cluster_bundle_pattern",
        "wallet_cluster_sell_pressure_high",
        "wallet_cluster_volume_liquidity_spike",
        "wallet_cluster_dumping",
        "wallet_cluster_risk_score_high",
    }
    severe_toxic_markers = toxic_markers - {"wallet_cluster_single_holder_dominant"}
    blocker_set = set(blockers)
    constructive = (
        liq >= policy.heating_min_liq_usd
        and buys5m >= policy.route_market_support_min_buys5m
        and buyers_5m >= max(candidate_policy.min_unique_buyers_5m + 2, 5)
        and (sell_ratio == 0.0 or sell_ratio <= policy.adversarial_max_sell_ratio_5m)
        and (vol_liq_ratio == 0.0 or vol_liq_ratio <= policy.adversarial_max_vol_liq_ratio_5m)
        and price_change_m5 >= -18.0
        and (risk is None or risk <= 0.60)
    )
    toxic = bool(severe_toxic_markers & blocker_set) or (
        "wallet_cluster_single_holder_dominant" in blocker_set and not constructive
    )
    if toxic:
        verdict = "toxic_cluster"
    elif constructive and smart_wallet_hits > 0:
        verdict = "smart_accumulation"
    elif constructive and (len(signals) >= 4 or unique_wallets_30s >= 4):
        verdict = "coordinated_accumulation"
    else:
        verdict = "uncertain_concentration"
        if "wallet_cluster_insufficient_constructive_flow" not in blockers:
            blockers.append("wallet_cluster_insufficient_constructive_flow")

    return {
        "verdict": verdict,
        "score": max(-100, min(100, score)),
        "signals": list(dict.fromkeys(signals)),
        "blockers": list(dict.fromkeys(blockers)),
        "metrics": {
            "wallet_risk": wallet_level or None,
            "top_holder_pct": top_holder_pct,
            "top10_pct": top10_pct,
            "total_buys_30s": total_buys_30s,
            "unique_wallets_30s": unique_wallets_30s,
            "top_wallet_share_30s": round(float(top_wallet_share or 0.0), 4),
            "unique_buyers_5m": buyers_5m,
            "burst_count_60s": burst_60s,
            "tracked_wallet_hits": tracked_hits,
            "kol_wallet_hits": kol_hits,
            "liquidity_usd": liq,
            "txns_m5_buys": buys5m,
            "txns_m5_sells": sells5m,
            "sell_ratio_5m": round(sell_ratio, 4),
            "volume_liquidity_ratio_5m": round(vol_liq_ratio, 4),
            "price_change_m5": price_change_m5,
            "risk_score": risk,
        },
    }


WALLET_GUARD_REASONS = {
    "wallet_distribution_high_risk",
    "wallet_top_holder_concentration",
}


def _wallet_guard_category(
    reasons: list[str],
    *,
    wallet_observe_ok: bool = False,
    attention_metrics: dict[str, Any] | None = None,
    wallet_cluster_review: dict[str, Any] | None = None,
) -> str:
    reason_set = {str(reason) for reason in reasons}
    metrics = attention_metrics if isinstance(attention_metrics, dict) else {}
    smart_wallet_hits = int(metrics.get("tracked_wallet_hits") or 0) + int(metrics.get("kol_wallet_hits") or 0)
    cluster = wallet_cluster_review if isinstance(wallet_cluster_review, dict) else {}
    verdict = str(cluster.get("verdict") or "").strip()
    if reason_set & {"mint_authority_active", "freeze_authority_active", "bundle_pattern_detected"}:
        return "hard_fraud"
    if verdict == "toxic_cluster":
        return "toxic_wallet_cluster"
    if verdict == "smart_accumulation":
        return "smart_accumulation"
    if verdict == "coordinated_accumulation":
        return "coordinated_accumulation"
    if wallet_observe_ok:
        return "smart_accumulation" if smart_wallet_hits > 0 else "early_concentration"
    if reason_set & WALLET_GUARD_REASONS:
        return "early_concentration"
    if "liquidity_unknown" in reason_set:
        return "unknown_wallet_structure"
    return "none"


def _wallet_guard_observe_decision(
    hard_fail_reasons: list[str],
    *,
    attention_score: float | None,
    risk_score: float | None,
    attention_metrics: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
    wallet_cluster_review: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    reasons = list(dict.fromkeys(hard_fail_reasons))
    if not reasons or not set(reasons).issubset(WALLET_GUARD_REASONS):
        return False, ["non_wallet_hard_fail"]
    cluster = wallet_cluster_review if isinstance(wallet_cluster_review, dict) else {}
    verdict = str(cluster.get("verdict") or "").strip()
    if verdict == "toxic_cluster":
        return False, [*(cluster.get("blockers") if isinstance(cluster.get("blockers"), list) else []), "wallet_cluster_toxic"]
    if verdict and verdict not in {"smart_accumulation", "coordinated_accumulation"}:
        return False, [*(cluster.get("blockers") if isinstance(cluster.get("blockers"), list) else []), "wallet_cluster_not_constructive"]

    metrics = attention_metrics if isinstance(attention_metrics, dict) else {}
    dex = dex_summary if isinstance(dex_summary, dict) else {}
    policy = route_signal_policy()
    candidate_policy = candidate_signal_policy()

    liq = float(dex.get("liquidity_usd") or 0.0)
    buys5m = int(dex.get("txns_m5_buys") or 0)
    sells5m = int(dex.get("txns_m5_sells") or 0)
    vol5m = float(dex.get("volume_m5") or 0.0)
    price_change_m5 = float(dex.get("price_change_m5") or 0.0)
    attn = float(attention_score or 0.0)
    risk = float(risk_score) if isinstance(risk_score, (int, float)) else 1.0
    buyers_5m = int(metrics.get("unique_buyers_5m") or 0)
    tracked_hits = int(metrics.get("tracked_wallet_hits") or 0)
    kol_hits = int(metrics.get("kol_wallet_hits") or 0)
    burst_60s = int(metrics.get("burst_count_60s") or 0)

    blockers: list[str] = []
    if liq < policy.heating_min_liq_usd:
        blockers.append(f"wallet_observe_liquidity<{policy.heating_min_liq_usd:.0f}")
    if buys5m < policy.route_market_support_min_buys5m:
        blockers.append(f"wallet_observe_buys5m<{policy.route_market_support_min_buys5m}")
    if buys5m > 0 and (float(sells5m) / float(buys5m)) > policy.adversarial_max_sell_ratio_5m:
        blockers.append(f"wallet_observe_sell_ratio>{policy.adversarial_max_sell_ratio_5m:.2f}")
    if liq > 0.0 and (vol5m / liq) > policy.adversarial_max_vol_liq_ratio_5m:
        blockers.append(f"wallet_observe_vol_liq_ratio>{policy.adversarial_max_vol_liq_ratio_5m:.2f}")
    if price_change_m5 < -18.0:
        blockers.append("wallet_observe_price_change_m5<-18")
    if risk > 0.60:
        blockers.append("wallet_observe_risk>0.60")

    has_attention = attn >= policy.heating_min_attention
    has_breadth = buyers_5m >= max(candidate_policy.min_unique_buyers_5m + 1, 4)
    has_smart_wallet = tracked_hits > 0 or kol_hits > 0
    has_burst = burst_60s >= policy.route_burst_strength_min
    if not (has_attention or (has_breadth and has_burst) or has_smart_wallet):
        blockers.append("wallet_observe_confirmation_missing")

    return not blockers, blockers


def _candidate_send_eligible(
    attention_score: float | None,
    creator_score: float,
    creator_min: float = EARLY_CREATOR_MIN,
) -> bool:
    attn = float(attention_score or 0.0)
    # Creator quality can help borderline setups, but should not push weak attention through on its own.
    return attn >= 0.50 or (creator_score >= creator_min and attn >= 0.35)


def _candidate_send_decision(
    *,
    attention_score: float | None,
    creator_score: float,
    extra: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> tuple[bool, list[str], list[str]]:
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=attention_score,
        creator_score=creator_score,
        extra=extra,
        dex_summary=dex_summary,
    )
    return eligible, reasons, confirmations


def _apply_candidate_ev_gate(
    *,
    send_eligible: bool,
    send_reasons: list[str],
    extra: dict[str, Any],
    dex_summary: dict[str, Any] | None,
    attention_score: float | None,
    risk_score: float | None,
) -> tuple[bool, list[str], dict[str, Any]]:
    trade_validation_payload = (
        extra.get("trade_validation") if isinstance(extra.get("trade_validation"), dict) else None
    )
    candidate_ev = evaluate_candidate_ev(
        trade_validation_payload,
        attention_score=attention_score,
        risk_score=risk_score,
        dex_summary=dex_summary,
        watch_override=isinstance(extra.get("watch_override"), dict),
    )
    extra["candidate_ev"] = candidate_ev
    if send_eligible and not candidate_ev.get("approved"):
        ev_reasons = [
            f"ev_gate:{reason}"
            for reason in candidate_ev.get("reasons", [])
            if reason != "ev_gate_passed"
        ]
        send_eligible = False
        send_reasons = list(dict.fromkeys([*send_reasons, *ev_reasons]))
    return send_eligible, send_reasons, candidate_ev


def _route_precedence_rank(event: Event) -> int:
    extra = event.extra if isinstance(event.extra, dict) else {}
    route = extra.get("route_decision") if isinstance(extra.get("route_decision"), dict) else {}
    tier = str(route.get("tier") or "").strip().lower()
    if event.type == "promoted":
        return 4
    if event.type == "heating_up" and tier == "sniper":
        return 3
    if event.type == "candidate":
        return 2
    if event.type == "heating_up":
        return 1
    return 0


def _apply_route_precedence(events: list[Event]) -> list[Event]:
    if not events:
        return events
    by_token: dict[str, list[Event]] = {}
    passthrough: list[Event] = []
    for event in events:
        token = str(event.token or "").strip()
        if not token:
            passthrough.append(event)
            continue
        by_token.setdefault(token, []).append(event)

    resolved: list[Event] = list(passthrough)
    for token, token_events in by_token.items():
        has_promoted = any(event.type == "promoted" for event in token_events)
        has_sniper_heating = any(
            event.type == "heating_up"
            and str((((event.extra if isinstance(event.extra, dict) else {}) or {}).get("route_decision") or {}).get("tier") or "").strip().lower() == "sniper"
            for event in token_events
        )
        for event in token_events:
            if has_promoted and event.type != "promoted":
                continue
            if has_sniper_heating and event.type == "candidate":
                continue
            resolved.append(event)
    return sorted(resolved, key=_route_precedence_rank, reverse=True)


def _token_is_tradeable_target(meta: dict | None, dex_summary: Dict[str, Any] | None) -> bool:
    if dex_summary:
        return True
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("is_fungible"))


def _has_bonding_curve_evidence(e: Event, extra: Dict[str, Any]) -> bool:
    if isinstance(extra.get("bonding_curve_present"), bool):
        return bool(extra.get("bonding_curve_present"))
    reasons = set()
    for reason in e.reasons or []:
        if isinstance(reason, str) and reason.strip():
            reasons.add(reason.strip())
    markers = {
        "mint_resolved_from_tx",
        "mint_resolved_from_logs_lookup",
        "mint_resolved_from_logs_retry",
        "pump_program_seen_in_tx",
        "InitializeMint_in_logs",
    }
    if reasons & markers:
        return True
    source = str(e.source or "").strip().lower()
    event_type = str(e.type or "").strip().lower()
    if source in {"logs", "tx"} and event_type in {"token_resolved", "early_tx_pump_observed", "early_logs_initialize_mint"}:
        return True
    return False


def _record_decision(
    e: Event,
    *,
    stage: str,
    decision: str,
    action_taken: str | None = None,
    reasons: list[str] | None = None,
    attention_score: float | None = None,
    risk_score: float | None = None,
    confidence_score: float | None = None,
    creator_score: float | None = None,
    lifecycle: str | None = None,
    policy_name: str | None = None,
    policy_version: str | None = None,
) -> None:
    extra = e.extra if isinstance(e.extra, dict) else {}
    dex_summary = extra.get("dex_summary") if isinstance(extra.get("dex_summary"), dict) else {}
    attention_metrics = extra.get("attention_metrics") if isinstance(extra.get("attention_metrics"), dict) else {}
    route_decision = extra.get("route_decision") if isinstance(extra.get("route_decision"), dict) else {}
    entry_quality = route_decision.get("entry_quality") if isinstance(route_decision.get("entry_quality"), dict) else {}
    wallet_cluster = extra.get("wallet_cluster_review") if isinstance(extra.get("wallet_cluster_review"), dict) else {}
    trade_validation = extra.get("trade_validation") if isinstance(extra.get("trade_validation"), dict) else {}
    candidate_ev = extra.get("candidate_ev") if isinstance(extra.get("candidate_ev"), dict) else {}
    buy_quote = trade_validation.get("buy_quote") if isinstance(trade_validation.get("buy_quote"), dict) else {}
    sell_quote = trade_validation.get("sell_quote") if isinstance(trade_validation.get("sell_quote"), dict) else {}
    route_labels = []
    for quote in (buy_quote, sell_quote):
        labels = quote.get("route_labels") if isinstance(quote.get("route_labels"), list) else []
        route_labels.extend(str(label) for label in labels if label)
    features = {
        "attention_score": attention_score,
        "risk_score": risk_score,
        "confidence_score": confidence_score,
        "creator_score": creator_score,
        "lifecycle": lifecycle,
        "market_cap_usd": dex_summary.get("market_cap_usd") or dex_summary.get("market_cap") or dex_summary.get("fdv"),
        "price_usd": dex_summary.get("price_usd"),
        "liquidity_usd": dex_summary.get("liquidity_usd"),
        "volume_m5_usd": dex_summary.get("volume_m5"),
        "volume_h1_usd": dex_summary.get("volume_h1"),
        "age_minutes": dex_summary.get("age_minutes"),
        "price_change_m5": dex_summary.get("price_change_m5"),
        "price_change_h1": dex_summary.get("price_change_h1"),
        "txns_m5_buys": dex_summary.get("txns_m5_buys"),
        "txns_m5_sells": dex_summary.get("txns_m5_sells"),
        "txns_h1_buys": dex_summary.get("txns_h1_buys"),
        "txns_h1_sells": dex_summary.get("txns_h1_sells"),
        "unique_buyers_5m": attention_metrics.get("unique_buyers_5m"),
        "unique_buyers_15m": attention_metrics.get("unique_buyers_15m"),
        "burst_count_60s": attention_metrics.get("burst_count_60s"),
        "tracked_wallet_hits": attention_metrics.get("tracked_wallet_hits"),
        "kol_wallet_hits": attention_metrics.get("kol_wallet_hits"),
        "discovery_sources": attention_metrics.get("discovery_sources") or [],
        "community_takeover": attention_metrics.get("community_takeover"),
        "paid_visibility": attention_metrics.get("paid_visibility"),
        "paid_visibility_class": attention_metrics.get("paid_visibility_class"),
        "source_stability": attention_metrics.get("source_stability"),
        "independent_flow_confirmed": attention_metrics.get("independent_flow_confirmed"),
        "dex_scan_repeat_count": attention_metrics.get("dex_scan_repeat_count"),
        "dex_scan_persistent": attention_metrics.get("dex_scan_persistent"),
        "dex_scan_momentum_slope": attention_metrics.get("dex_scan_momentum_slope"),
        "dex_scan_first_seen_age_seconds": attention_metrics.get("dex_scan_first_seen_age_seconds"),
        "dex_scan_volume_delta_5m": attention_metrics.get("dex_scan_volume_delta_5m"),
        "dex_scan_liquidity_delta_pct": attention_metrics.get("dex_scan_liquidity_delta_pct"),
        "sells_5m": attention_metrics.get("sells_5m"),
        "sell_ratio_5m": attention_metrics.get("sell_ratio_5m"),
        "x_query_attempted": attention_metrics.get("x_query_attempted"),
        "x_query_reason": attention_metrics.get("x_query_reason"),
        "x_signal_available": attention_metrics.get("x_signal_available"),
        "x_tweet_count": attention_metrics.get("x_tweet_count"),
        "x_unique_authors": attention_metrics.get("x_unique_authors"),
        "x_heavy_author_count": attention_metrics.get("x_heavy_author_count"),
        "x_verified_author_count": attention_metrics.get("x_verified_author_count"),
        "x_author_followers": attention_metrics.get("x_author_followers"),
        "dex_scan_reason": (extra.get("dex_scan_candidate") or {}).get("reason") if isinstance(extra.get("dex_scan_candidate"), dict) else None,
        "dex_source_health": attention_metrics.get("dex_source_health") or ((extra.get("metrics") or {}).get("dex_source_health") if isinstance(extra.get("metrics"), dict) else None),
        "candidate_send_eligible": extra.get("candidate_send_eligible"),
        "candidate_send_final": extra.get("candidate_send"),
        "candidate_edit": extra.get("candidate_edit"),
        "candidate_improved": extra.get("candidate_improved"),
        "candidate_rate_limit_allowed": extra.get("candidate_rate_limit_allowed"),
        "candidate_rate_limit_checked": extra.get("candidate_rate_limit_checked"),
        "candidate_progression_ok": extra.get("candidate_progression_ok"),
        "candidate_confirmation_signals": extra.get("candidate_confirmation_signals") or [],
        "wallet_guard_category": extra.get("wallet_guard_category"),
        "wallet_guard_watch_only": bool(extra.get("wallet_guard_watch_only")),
        "wallet_guard_original_reasons": extra.get("wallet_guard_original_reasons") or [],
        "wallet_guard_observe_blockers": extra.get("wallet_guard_observe_blockers") or [],
        "wallet_cluster_verdict": wallet_cluster.get("verdict"),
        "wallet_cluster_score": wallet_cluster.get("score"),
        "wallet_cluster_signals": wallet_cluster.get("signals") or [],
        "wallet_cluster_blockers": wallet_cluster.get("blockers") or [],
        "wallet_cluster_metrics": wallet_cluster.get("metrics") if isinstance(wallet_cluster.get("metrics"), dict) else {},
        "watch_override": extra.get("watch_override") if isinstance(extra.get("watch_override"), dict) else None,
        "watch_override_gate_bypass": extra.get("watch_override_gate_bypass") or [],
        "has_dex_pool": bool(dex_summary),
        "lp_drain": extra.get("lp_drain"),
        "creator_sell": extra.get("creator_sold"),
        "trade_validation_approved": trade_validation.get("approved"),
        "trade_validation_reasons": trade_validation.get("reasons") or [],
        "trade_validation_warnings": trade_validation.get("warnings") or [],
        "trade_validation_size_usd": trade_validation.get("intended_size_usd"),
        "trade_validation_pair_address": trade_validation.get("pair_address"),
        "trade_validation_dex_id": trade_validation.get("dex_id"),
        "buy_slippage_bps": buy_quote.get("slippage_bps"),
        "sell_slippage_bps": sell_quote.get("slippage_bps"),
        "buy_price_impact_pct": buy_quote.get("price_impact_pct"),
        "sell_price_impact_pct": sell_quote.get("price_impact_pct"),
        "route_labels": list(dict.fromkeys(route_labels)),
        "entry_quality_tier": entry_quality.get("tier"),
        "entry_quality_score": entry_quality.get("score"),
        "entry_quality_reasons": entry_quality.get("reasons") or [],
        "entry_quality_supports": entry_quality.get("supports") or [],
        "candidate_ev_approved": candidate_ev.get("approved"),
        "candidate_ev_net_edge_bps": candidate_ev.get("net_edge_bps"),
        "candidate_ev_gross_upside_bps": candidate_ev.get("gross_upside_bps"),
        "candidate_ev_cost_bps": candidate_ev.get("cost_bps"),
        "candidate_ev_risk_penalty_bps": candidate_ev.get("risk_penalty_bps"),
        "candidate_ev_round_trip_slippage_bps": candidate_ev.get("round_trip_slippage_bps"),
        "candidate_ev_max_price_impact_pct": candidate_ev.get("max_price_impact_pct"),
        "candidate_ev_reasons": candidate_ev.get("reasons") or [],
    }
    features.update(classify_policy_regime(features, stage=stage, ts_value=e.ts))
    log_event(
        logger,
        logging.INFO,
        "decision",
        token=e.token,
        event_type=e.type,
        source=e.source,
        stage=stage,
        decision=decision,
        action_taken=action_taken,
        reasons=reasons or [],
        attention_score=attention_score,
        risk_score=risk_score,
        confidence_score=confidence_score,
        creator_score=creator_score,
        lifecycle=lifecycle,
        route_tier=route_decision.get("tier"),
        route_confidence=route_decision.get("route_confidence"),
        route_confirmations=route_decision.get("confirmations") or [],
        route_blockers=route_decision.get("blockers") or [],
        sniper_blockers=route_decision.get("sniper_blockers") or [],
        sniper_ready=route_decision.get("sniper_ready"),
        sniper_fast_track=route_decision.get("sniper_fast_track"),
        age_bypass_eligible=route_decision.get("age_bypass_eligible"),
        age_bypass_ttl_sec=route_decision.get("age_bypass_ttl_sec"),
        age_bypass_until=extra.get("age_bypass_until"),
        candidate_send=extra.get("candidate_send"),
        policy_name=policy_name,
        policy_version=policy_version,
        features=features,
    )
    try:
        signal_id = record_signal_decision(
            token=e.token,
            event_type=e.type,
            stage=stage,
            decision=decision,
            action_taken=action_taken,
            reasons=reasons,
            features=features,
            policy_name=policy_name,
            policy_version=policy_version,
            attention_score=attention_score,
            risk_score=risk_score,
            confidence_score=confidence_score,
            creator_score=creator_score,
            lifecycle=lifecycle,
            ts_value=e.ts,
            signal_id=str((e.extra or {}).get("_signal_id") or "").strip() or None,
            source=e.source,
            creator=e.creator,
        )
        if signal_id and isinstance(e.extra, dict):
            e.extra["_signal_id"] = signal_id
    except Exception:
        logger.exception("[diagnostics] record_signal_decision_failed token=%s decision=%s", e.token, decision)

async def process_event(state: EngineState, e: Event) -> list[Event]:
    """
    Enrich, score, gate, and route one event into zero or more downstream
    engine events.

    This function is the main runtime decision boundary between ingestion and
    delivery. See the module docstring for the full control-flow map.
    """
    out: list[Event] = []
    logger.info("[PROMOTE HANDLER CALLED] type=%s token=%s", e.type, e.token)
    logger.info("[promote-enter] token=%s", e.token)
    dex_summary = {}
    attention_metrics = {}
    if isinstance(e.extra, dict):
        dex_summary = e.extra.get("dex_summary") if isinstance(e.extra.get("dex_summary"), dict) else {}
        attention_metrics = e.extra.get("attention_metrics") if isinstance(e.extra.get("attention_metrics"), dict) else {}
    candidate_regime = classify_policy_regime(
        {
            "session_bucket": (e.extra or {}).get("session_bucket") if isinstance(e.extra, dict) else None,
            "liquidity_usd": dex_summary.get("liquidity_usd"),
            "age_minutes": dex_summary.get("age_minutes"),
            "price_change_m5": dex_summary.get("price_change_m5"),
            "price_change_h1": dex_summary.get("price_change_h1"),
            "unique_buyers_15m": attention_metrics.get("unique_buyers_15m"),
            "txns_m5_buys": dex_summary.get("txns_m5_buys"),
        },
        stage="candidate",
        ts_value=e.ts,
    )
    promoted_regime = classify_policy_regime(
        {
            "session_bucket": (e.extra or {}).get("session_bucket") if isinstance(e.extra, dict) else None,
            "liquidity_usd": dex_summary.get("liquidity_usd"),
            "age_minutes": dex_summary.get("age_minutes"),
            "price_change_m5": dex_summary.get("price_change_m5"),
            "price_change_h1": dex_summary.get("price_change_h1"),
            "unique_buyers_15m": attention_metrics.get("unique_buyers_15m"),
            "txns_m5_buys": dex_summary.get("txns_m5_buys"),
        },
        stage="promoted",
        ts_value=e.ts,
    )
    candidate_policy = resolve_live_policy("candidate", e.token, regime_key=candidate_regime["regime_key"])
    promoted_policy = resolve_live_policy("promoted", e.token, regime_key=promoted_regime["regime_key"])
    candidate_config = candidate_policy.get("config") if isinstance(candidate_policy.get("config"), dict) else {}
    promoted_config = promoted_policy.get("config") if isinstance(promoted_policy.get("config"), dict) else {}
    creator_min = float(candidate_config.get("candidate_creator_min") or EARLY_CREATOR_MIN)
    promoted_confidence_min = float(promoted_config.get("promoted_confidence_min") or 0.80)
    promoted_attention_min = float(promoted_config.get("promoted_attention_min") or PROMOTION_MIN_ATTENTION)
    promoted_risk_max = float(promoted_config.get("promoted_risk_max") or PROMOTION_MAX_RISK)
    promoted_liquidity_min = float(promoted_config.get("promoted_liquidity_min") or PROM_MIN_LIQ_USD)
    promoted_buyers_15m_min = int(promoted_config.get("promoted_buyers_15m_min") or 30)

    # Early events without token: state only, never alert or promote
    if e.type.startswith("early") and not e.token:
        logger.info("[promote-skip] reason=token_unresolved")
        if e.type == "early_logs_initialize_mint":
            e.confidence = bump(e.confidence, CONF_WEIGHTS["logs_initialize_mint"], CAPS["early"])
            e.reasons.append("logs_initialize_mint")
        elif e.type == "early_tx_pump_observed":
            e.confidence = bump(e.confidence, CONF_WEIGHTS["tx_pump_observed"], CAPS["early"])
            e.reasons.append("tx_pump_observed")
        return [e]

    # Token present: update state and decide promotion
    if e.token:
        logger.info("[score] evaluating token=%s", e.token)
        ts = bump_token(
            state,
            e.token,
            delta_conf=0.0,
            reason=e.type,
            creator=e.creator,
        )
        if e.type == "token_resolved":
            meta = fetch_token_metadata(e.token)
            if meta:
                e.extra["token_interface"] = meta.get("interface")
                e.extra["token_is_fungible"] = bool(meta.get("is_fungible"))
            if meta:
                symbol = meta.get("symbol") or ""
                name = meta.get("name") or ""
                if symbol:
                    ts.symbol = symbol
                    e.extra["symbol"] = symbol
                if name:
                    ts.name = name
                    e.extra["name"] = name
                logger.info(
                    "[token-metadata] token=%s symbol=%s name=%s",
                    e.token,
                    symbol or "unknown",
                    name or "unknown",
                )
        if ts.symbol and not e.extra.get("symbol"):
            e.extra["symbol"] = ts.symbol


        if ts.name and not e.extra.get("name"):
            e.extra["name"] = ts.name

        st = _ts(e.token)
        now_wall = time.time()
        if now_wall < st.blacklist_until:
            log_event(logger, logging.INFO, "blacklist-skip", token=e.token, blacklist_until=st.blacklist_until)
            _record_decision(e, stage="routing", decision="blacklist_skip", reasons=["blacklisted"], lifecycle=str((e.extra or {}).get("lifecycle") or "unknown"))
            return out
        if e.creator and e.type == "token_resolved" and ts.signals == 1:


            record_creator_deploy(e.creator)

        e.extra["metric_states"] = dict(e.extra.get("metric_states") or {})
        risk_score = None
        risk_reasons: list[str] = []
        risk_flags: Dict[str, bool] = {}
        if not ENABLE_FORENSICS:
            e.extra["metric_states"]["risk_score"] = metric_state(
                None,
                status="disabled",
                reason="forensics_disabled",
            )

        creator_score_info = {"score": 0.0, "reasons": ["creator_unknown"], "stats": {}}
        if e.creator:
            creator_score_info = compute_creator_score(e.creator)
            score_val = float(creator_score_info.get("score") or 0.0)
            e.extra["creator_score"] = score_val
            e.extra["creator_reasons"] = creator_score_info.get("reasons") or []
            e.extra["creator_stats"] = creator_score_info.get("stats") or {}
            logger.info(
                "[creator-score] token=%s creator=%s score=%.2f reasons=%s stats=%s",
                e.token,
                e.creator,
                score_val,
                e.extra["creator_reasons"],
                e.extra["creator_stats"],
            )


        buy_size_sol = 0.0
        buyer = e.extra.get("buyer") if isinstance(e.extra, dict) else None
        if buyer:
            sol_spent = None
            delta_raw = None
            decimals = None
            if isinstance(e.extra, dict):
                sol_spent = e.extra.get("sol_spent")
                delta_raw = e.extra.get("delta_raw")
                decimals = e.extra.get("decimals")
            try:
                if sol_spent is not None:
                    sol_spent = float(sol_spent)
            except Exception:
                sol_spent = None
            buy_size_sol = float(sol_spent or 0.0)
            min_sol = (
                MIN_BUY_SOL_FOR_ATTENTION_SNIPER
                if ENGINE_MODE in ("sniper", "balanced")
                else MIN_BUY_SOL_FOR_ATTENTION_LONG
            )
            if buy_size_sol < min_sol:
                log_event(logger, logging.INFO, "skip-attention", token=e.token, sol=buy_size_sol, min_sol=min_sol)
            else:
                weight = register_buyer(e.token, buyer, buy_size_sol)
                state.record_buyer(e.token, buyer, ts=e.ts, weight=weight)

        attention_score = None
        attn_reasons: list[str] = []
        attn_metrics: Dict[str, Any] = {}
        if ENABLE_ATTENTION:
            attention_score, attn_reasons, attn_metrics = compute_attention(e, state)
            logger.info(
                "[attention] token=%s attention=%.2f reasons=%s metrics=%s",
                e.token,
                attention_score,
                attn_reasons,
                attn_metrics,
            )
            logger.info(
                "[attention-metrics] token=%s unique_buyers_5m=%s burst_count_60s=%s dexscreener_boosts=%s",
                e.token,
                attn_metrics.get("unique_buyers_5m"),
                attn_metrics.get("burst_count_60s"),
                attn_metrics.get("dexscreener_boosts_count"),
            )
            e.extra["attention_score"] = attention_score
            e.extra["attention_reasons"] = attn_reasons
            e.extra["attention_metrics"] = attn_metrics
            e.extra["metric_states"]["attention_score"] = metric_state(
                attention_score,
                status="computed",
                reasons=attn_reasons,
            )
        else:
            e.extra["metric_states"]["attention_score"] = metric_state(
                None,
                status="disabled",
                reason="attention_disabled",
            )

        edge_bps = 0.0
        edge_reasons: list[str] = []
        size_cap_usd = 0.0
        if ENABLE_EXECUTION:
            edge_bps, edge_reasons, size_cap_usd = estimate_edge(e, state)
            logger.info(
                "[execution] token=%s edge_bps=%.1f size_cap_usd=%.0f reasons=%s",
                e.token,
                edge_bps,
                size_cap_usd,
                edge_reasons,
            )
            e.extra["edge_bps"] = edge_bps
            e.extra["edge_reasons"] = edge_reasons
            e.extra["size_cap_usd"] = size_cap_usd

        if e.type == "token_resolved":
            e.confidence = bump(e.confidence, CONF_WEIGHTS["token_resolved"], CAPS["heating"])
            e.reasons.append("token_resolved")

        extra: Dict[str, Any] = e.extra if isinstance(e.extra, dict) else {}
        e.extra = extra
        best_pair = None
        if ENABLE_DEX:
            extra["dex"] = await dex_enrich_token(e.token)
            dex_summary = None
            try:
                if isinstance(extra.get("dex"), dict):
                    best_pair = select_best_pair(extra["dex"], e.token)
                    if best_pair:
                        dex_summary = summarize_pair(best_pair)
            except Exception:
                dex_summary = None
            if dex_summary:
                extra["dex_summary"] = dex_summary
            if extra.get("dex", {}).get("ok"):
                e.confidence = bump(
                    e.confidence, CONF_WEIGHTS["dex_pair_found"], CAPS["heating"]
                )
                e.reasons.append("dex_pair_found")
        else:
            dex_summary = None
        token_meta = fetch_token_metadata(e.token) if e.token else None
        if token_meta:
            extra["token_interface"] = token_meta.get("interface")
            extra["token_is_fungible"] = bool(token_meta.get("is_fungible"))
        token_is_tradeable = _token_is_tradeable_target(token_meta, dex_summary)
        extra["token_is_tradeable"] = token_is_tradeable
        bonding_curve_verified = _has_bonding_curve_evidence(e, extra)
        extra["bonding_curve_present"] = bonding_curve_verified
        extra["market_target"] = "dex" if dex_summary else "pump_fun" if bonding_curve_verified else "unverified"

        # Elite layer: structural safety + score + age bypass + decay
        hard_fail = False
        hard_fail_from_authority_checks = False
        hard_fail_reasons: list[str] = []
        try:
            mint_auth, freeze_auth = ELITE.auth_check(e.token)
            log_event(logger, logging.INFO, "auth-check", token=e.token, mint_auth=bool(mint_auth), freeze_auth=bool(freeze_auth))
            if mint_auth:
                log_event(logger, logging.INFO, "risk-gate", token=e.token, reason="mint_authority_active")
                hard_fail = True
                hard_fail_from_authority_checks = True
                hard_fail_reasons.append("mint_authority_active")
            if freeze_auth:
                log_event(logger, logging.INFO, "risk-gate", token=e.token, reason="freeze_authority_active")
                hard_fail = True
                hard_fail_from_authority_checks = True
                hard_fail_reasons.append("freeze_authority_active")
        except Exception:
            mint_auth = None
            freeze_auth = None

        try:
            liq_usd, liq_locked, liq_drop = ELITE.liq_check(e.token, dex_summary)
            log_event(logger, logging.INFO, "liq-check", token=e.token, liq_usd=liq_usd, liq_locked=liq_locked, liq_drop=liq_drop)
            liquidity_unknown = False
            if liq_usd == 0:
                liquidity_unknown = True
            elif liq_usd < 15000:
                log_event(logger, logging.INFO, "risk-gate", token=e.token, reason="low_liquidity", liq_usd=liq_usd)
                hard_fail = True
                hard_fail_reasons.append("low_liquidity")
            if not liquidity_unknown:
                if liq_drop:
                    log_event(logger, logging.INFO, "risk-gate", token=e.token, reason="liq_drop_spike")
                    hard_fail = True
                    hard_fail_reasons.append("liq_drop_spike")
                if liq_locked is False:
                    log_event(logger, logging.INFO, "risk-gate", token=e.token, reason="liq_unlocked")
                    hard_fail = True
                    hard_fail_reasons.append("liq_unlocked")
        except Exception:
            liq_usd = None
            liq_locked = None
            liq_drop = None
            liquidity_unknown = True


        unique_10s = 0
        burst_10s = 0
        unique_30s = 0
        top_share = 0.0
        now_wall = time.time()
        try:
            while st.buyers_10s and now_wall - st.buyers_10s[0][1] > 10:
                st.buyers_10s.popleft()
            unique_10s = len({b for b, _ in st.buyers_10s})

            while st.burst_10s and now_wall - st.burst_10s[0][0] > 10:
                st.burst_10s.popleft()
            burst_10s = sum(x[1] for x in st.burst_10s)

            while st.buys_30s and now_wall - st.buys_30s[0][1] > 30:
                st.buys_30s.popleft()

            counts: Dict[str, int] = {}
            for b, _, _, _ in st.buys_30s:
                counts[b] = counts.get(b, 0) + 1
            total = len(st.buys_30s)
            unique_30s = len(counts)
            top_share = (max(counts.values()) / total) if total else 0.0
        except Exception:
            pass

        token_wallet_risk = wallet_risk_score(e.token)
        wallet_cluster = _wallet_cluster_review(
            token_wallet_risk,
            total_buys_30s=len(st.buys_30s),
            unique_wallets_30s=unique_30s,
            top_wallet_share=top_share,
            attention_metrics=attn_metrics,
            dex_summary=dex_summary,
            risk_score=risk_score,
        )
        extra["wallet_cluster_review"] = wallet_cluster
        distribution_fail_reasons = _wallet_distribution_fail_reasons(
            token_wallet_risk,
            total_buys_30s=len(st.buys_30s),
            unique_wallets_30s=unique_30s,
            top_wallet_share=top_share,
        )
        for reason in distribution_fail_reasons:
            log_event(logger, logging.INFO, "risk-gate", token=e.token, reason=reason)
        if distribution_fail_reasons:
            hard_fail = True
            hard_fail_reasons.extend(distribution_fail_reasons)
        if ENABLE_FORENSICS:
            risk_score, risk_reasons, risk_flags, risk_metric = analyze_risk(
                e,
                state,
                wallet_risk=token_wallet_risk,
                mint_authority=mint_auth,
                freeze_authority=freeze_auth,
                liq_usd=liq_usd,
                liq_locked=liq_locked,
                liq_drop_spike=liq_drop,
            )
            creator_risk_offset = float(creator_score_info.get("score") or 0.0) * CREATOR_RISK_WEIGHT
            if risk_score is not None:
                risk_score = max(0.0, risk_score - creator_risk_offset)
            e.extra["risk_score"] = risk_score
            e.extra["risk_reasons"] = risk_reasons
            e.extra["risk_flags"] = risk_flags
            e.extra["token_wallet_risk"] = token_wallet_risk
            e.extra["metric_states"]["risk_score"] = metric_state(
                risk_score,
                status=str(risk_metric.get("status") or "not_computed"),
                reason=risk_metric.get("reason"),
                reasons=risk_reasons,
            )
            logger.info(
                "[risk-score] token=%s status=%s value=%s reasons=%s inputs=%s creator_offset=%.3f",
                e.token,
                risk_metric.get("status"),
                risk_score,
                risk_reasons,
                risk_metric.get("inputs_used"),
                creator_risk_offset,
            )
            extra["risk_score"] = risk_score
            extra["risk_reasons"] = risk_reasons
            extra["risk_flags"] = risk_flags
            extra["token_wallet_risk"] = token_wallet_risk
            extra["metric_states"] = dict(e.extra.get("metric_states") or {})
            if distribution_fail_reasons:
                for reason in distribution_fail_reasons:
                    if reason not in risk_reasons:
                        risk_reasons.append(reason)
                if (
                    "wallet_top_holder_concentration" in distribution_fail_reasons
                    or "wallet_distribution_high_risk" in distribution_fail_reasons
                ):
                    risk_flags["holder_concentration"] = True
                if "bundle_pattern_detected" in distribution_fail_reasons:
                    risk_flags["wallet_cluster"] = True

        top_holder_ratio = None
        try:
            top_holder_ratio = float(state.top_holder_ratio(e.token))
        except Exception:
            top_holder_ratio = None
        if TRADE_VALIDATION_ENABLED:
            try:
                trade_validation = validate_trade(
                    token=e.token,
                    best_pair=best_pair,
                    dex_summary=dex_summary,
                    token_meta=token_meta,
                    risk_score=risk_score,
                    wallet_risk=token_wallet_risk,
                    mint_authority=mint_auth,
                    freeze_authority=freeze_auth,
                    top_holder_ratio=top_holder_ratio,
                )
            except Exception:
                logger.exception("[trade-validator-error] token=%s", e.token)
                trade_validation = {
                    "approved": False,
                    "token": e.token,
                    "policy_name": "elite_pretrade_validator",
                    "policy_version": "v1",
                    "validated_ts": int(time.time()),
                    "quote_expires_ts": int(time.time()),
                    "reasons": ["validator_exception"],
                    "warnings": [],
                    "checks": [],
                    "market_data": {},
                    "buy_quote": None,
                    "sell_quote": None,
                    "risk_summary": {},
                    "intended_size_usd": None,
                    "market_target": "unknown",
                    "pair_address": None,
                    "dex_id": None,
                }
            extra["trade_validation"] = trade_validation
            extra["trade_validation_approved"] = bool(trade_validation.get("approved"))

        elite_score = ELITE.compute_elite_score(
            token=e.token,
            buy_size_sol=buy_size_sol,
            unique_10s=unique_10s,
            total_buys_30s=len(st.buys_30s),
            unique_wallets_30s=unique_30s,
            top_wallet_share=top_share,
            liq_usd=0.0 if liq_usd is None else liq_usd,
            liq_locked=liq_locked,
            hard_fail=bool(hard_fail),
        )
        extra["elite_score"] = elite_score
        extra["metric_states"] = dict(extra.get("metric_states") or e.extra.get("metric_states") or {})
        extra["metric_states"]["elite_score"] = metric_state(elite_score, status="computed")
        extra["metric_states"]["lifecycle"] = metric_state(
            "dex" if dex_summary else "bonding_curve",
            status="computed",
        )

        if ENABLE_RISK_VETO and risk_score is not None and risk_score >= RISK_VETO_THRESHOLD:
            logger.info(
                "[promote-skip] reason=risk_veto token=%s risk=%.2f threshold=%.2f",
                e.token,
                risk_score,
                RISK_VETO_THRESHOLD,
            )
            _record_decision(
                e,
                stage="routing",
                decision="risk_veto_skip",
                reasons=["risk_veto"],
                attention_score=attention_score,
                risk_score=risk_score,
                confidence_score=e.confidence,
                creator_score=float(creator_score_info.get("score") or 0.0),
                lifecycle=str(extra.get("lifecycle") or "unknown"),
            )
            return [e]

        if liquidity_unknown:
            route_policy = route_signal_policy()
            candidate_policy = candidate_signal_policy()
            route_like_support = bool(
                (attention_score or 0.0) >= route_policy.sniper_min_attention
                or int(attn_metrics.get("tracked_wallet_hits") or 0) > 0
                or int(attn_metrics.get("kol_wallet_hits") or 0) > 0
                or int(attn_metrics.get("unique_buyers_5m") or 0) >= max(candidate_policy.min_unique_buyers_5m + 1, 4)
            )
            if (
                ENGINE_MODE == "balanced"
                and unique_10s >= route_policy.sniper_min_unique_10s
                and burst_10s >= route_policy.sniper_min_burst_10s
                and elite_score >= route_policy.sniper_min_elite
                and route_like_support
            ):
                log_event(
                    logger,
                    logging.INFO,
                    "liq-unknown-bypass",
                    token=e.token,
                    attention_score=attention_score,
                    tracked_wallet_hits=int(attn_metrics.get("tracked_wallet_hits") or 0),
                    kol_wallet_hits=int(attn_metrics.get("kol_wallet_hits") or 0),
                    unique_buyers_5m=int(attn_metrics.get("unique_buyers_5m") or 0),
                )
            else:
                hard_fail = True
                hard_fail_reasons.append("liquidity_unknown")

        if hard_fail and hard_fail_reasons:
            wallet_observe_ok, wallet_observe_blockers = _wallet_guard_observe_decision(
                hard_fail_reasons,
                attention_score=attention_score,
                risk_score=risk_score,
                attention_metrics=attn_metrics,
                dex_summary=dex_summary,
                wallet_cluster_review=extra.get("wallet_cluster_review") if isinstance(extra.get("wallet_cluster_review"), dict) else None,
            )
            extra["wallet_guard_original_reasons"] = list(dict.fromkeys(hard_fail_reasons))
            extra["wallet_guard_observe_blockers"] = wallet_observe_blockers
            extra["wallet_guard_category"] = _wallet_guard_category(
                hard_fail_reasons,
                wallet_observe_ok=wallet_observe_ok,
                attention_metrics=attn_metrics,
                wallet_cluster_review=extra.get("wallet_cluster_review") if isinstance(extra.get("wallet_cluster_review"), dict) else None,
            )
            if wallet_observe_ok:
                hard_fail = False
                hard_fail_reasons = []
                extra["wallet_guard_watch_only"] = True
                log_event(
                    logger,
                    logging.INFO,
                    "wallet-guard-observe",
                    token=e.token,
                    original_reasons=extra["wallet_guard_original_reasons"],
                    attention_score=attention_score,
                    risk_score=risk_score,
                    liquidity_usd=(dex_summary or {}).get("liquidity_usd") if isinstance(dex_summary, dict) else None,
                    buys5m=(dex_summary or {}).get("txns_m5_buys") if isinstance(dex_summary, dict) else None,
                    unique_buyers_5m=attn_metrics.get("unique_buyers_5m"),
                )

        override_reasons = list(extra.get("wallet_guard_original_reasons") or hard_fail_reasons)
        if override_reasons and set(override_reasons).issubset(WALLET_GUARD_REASONS) and (
            hard_fail or bool(extra.get("wallet_guard_watch_only"))
        ):
            dex = dex_summary if isinstance(dex_summary, dict) else {}
            watch_override = resolve_consumable_watch_override(
                e.token,
                market_cap_usd=dex.get("market_cap_usd") or dex.get("market_cap") or dex.get("fdv"),
                liquidity_usd=dex.get("liquidity_usd"),
            )
            if watch_override:
                hard_fail = False
                hard_fail_reasons = []
                extra["watch_override"] = {
                    "override_id": watch_override.get("override_id"),
                    "target_market_cap_usd": watch_override.get("target_market_cap_usd"),
                    "min_liquidity_usd": watch_override.get("min_liquidity_usd"),
                    "checks": watch_override.get("checks"),
                }
                extra["wallet_guard_watch_only"] = False
                extra["wallet_guard_category"] = str(
                    watch_override.get("wallet_category")
                    or extra.get("wallet_guard_category")
                    or "override"
                )
                if "watch_override_active" not in e.reasons:
                    e.reasons.append("watch_override_active")
                log_event(
                    logger,
                    logging.INFO,
                    "watch-override-consumed",
                    token=e.token,
                    override_id=watch_override.get("override_id"),
                    checks=watch_override.get("checks"),
                )

        if hard_fail:
            _record_decision(
                e,
                stage="routing",
                decision="hard_fail",
                reasons=hard_fail_reasons or ["hard_fail"],
                attention_score=attention_score,
                risk_score=risk_score,
                confidence_score=e.confidence,
                creator_score=float(creator_score_info.get("score") or 0.0),
                lifecycle=str(extra.get("lifecycle") or "unknown"),
            )
            return out

        age_bypassed = False
        if ENGINE_MODE == "long_term":
            st.age_bypass_until = 0.0
            age_bypassed = False
        else:
            age_bypassed = now_wall <= st.age_bypass_until
        extra["age_bypass_until"] = st.age_bypass_until if age_bypassed else 0.0
        route_decision = classify_route_signal(
            attention_score=attention_score,
            elite_score=elite_score,
            unique_10s=unique_10s,
            burst_10s=burst_10s,
            hard_fail_from_authority_checks=hard_fail_from_authority_checks,
            extra=extra,
            dex_summary=dex_summary,
        )
        extra["route_decision"] = route_decision
        if "wallet_guard_category" not in extra:
            extra["wallet_guard_category"] = _wallet_guard_category(
                list(extra.get("wallet_guard_original_reasons") or []),
                wallet_observe_ok=bool(extra.get("wallet_guard_watch_only")),
                attention_metrics=attn_metrics,
            )
        route_tier = str(route_decision.get("tier") or "watch")
        route_confidence = float(route_decision.get("route_confidence") or 0.0)
        age_bypass_eligible = bool(route_decision.get("age_bypass_eligible"))
        age_bypass_ttl_sec = int(route_decision.get("age_bypass_ttl_sec") or 0)
        age_bypass_reason = str(route_decision.get("age_bypass_reason") or "").strip()
        if ENGINE_MODE == "balanced" and age_bypass_eligible and age_bypass_ttl_sec > 0:
            desired_until = now_wall + age_bypass_ttl_sec
            if desired_until > st.age_bypass_until:
                st.age_bypass_until = desired_until
                log_event(
                    logger,
                    logging.INFO,
                    "age-bypass",
                    token=e.token,
                    tier=route_tier,
                    ttl=age_bypass_ttl_sec,
                    confidence=round(route_confidence, 3),
                    reason=age_bypass_reason,
                )
            age_bypassed = now_wall <= st.age_bypass_until
            extra["age_bypass_until"] = st.age_bypass_until
        else:
            age_bypassed = now_wall <= st.age_bypass_until
            extra["age_bypass_until"] = st.age_bypass_until if age_bypassed else 0.0
        log_event(
            logger,
            logging.INFO,
            "route-decision",
            token=e.token,
            tier=route_tier,
            confidence=round(route_confidence, 3),
            sniper_ready=bool(route_decision.get("sniper_ready")),
            fast_track=bool(route_decision.get("sniper_fast_track")),
            age_bypass_eligible=age_bypass_eligible,
            ttl=age_bypass_ttl_sec,
            confirmations=route_decision.get("confirmations") or [],
            blockers=route_decision.get("blockers") or [],
            sniper_blockers=route_decision.get("sniper_blockers") or [],
        )
        sniper_conditions_met = ENGINE_MODE == "balanced" and route_tier == "sniper"
        heating_conditions_met = ENGINE_MODE == "balanced" and route_tier in {"sniper", "heating_up"}
        if heating_conditions_met:
            if age_bypass_eligible and age_bypass_ttl_sec > 0 and now_wall > st.age_bypass_until:
                st.age_bypass_until = now_wall + age_bypass_ttl_sec
                extra["age_bypass_until"] = st.age_bypass_until
            log_event(
                logger,
                logging.INFO,
                "route-emit",
                token=e.token,
                tier=route_tier,
                confidence=round(route_confidence, 3),
                elite_score=elite_score,
                unique_10s=unique_10s,
                burst_10s=burst_10s,
                attention_score=attention_score,
                confirmations=route_decision.get("confirmations") or [],
                age_bypass_until=int(st.age_bypass_until or 0),
            )
            if route_tier == "sniper":
                log_event(
                    logger,
                    logging.INFO,
                    "discord-sniper",
                    token=e.token,
                    confidence=round(route_confidence, 3),
                    elite_score=elite_score,
                    unique_10s=unique_10s,
                    burst_10s=burst_10s,
                    confirmations=route_decision.get("confirmations") or [],
                )
                log_event(
                    logger,
                    logging.INFO,
                    "discord-send-attempt",
                    tier="sniper",
                    url_set=bool(DISCORD_WEBHOOK_URL),
                    token=e.token,
                    confidence=round(route_confidence, 3),
                )
            out.append(
                Event(
                    type="heating_up",
                    source="engine",
                    token=e.token,
                    creator=ts.creator,
                    confidence=e.confidence,
                    reasons=e.reasons + [f"{route_tier}_route"],
                    extra=dict(extra),
                    signature=e.signature,
                )
            )

        attention_policy = attention_scoring_policy()
        accel_boost = 0.0
        if unique_10s == attention_policy.acceleration_unique_3_min:
            accel_boost = attention_policy.acceleration_unique_3_boost
        elif unique_10s == attention_policy.acceleration_unique_4_min:
            accel_boost = attention_policy.acceleration_unique_4_boost
        elif unique_10s >= attention_policy.acceleration_unique_5_min:
            accel_boost = attention_policy.acceleration_unique_5_boost

        if elite_score >= SNIPER_MIN_ELITE_SCORE or accel_boost >= attention_policy.acceleration_unique_3_boost:
            if st.spike_started_at == 0:
                st.spike_started_at = now_wall
                st.last_unique_buyers = unique_10s
                st.last_burst_weight = burst_10s
                log_event(logger, logging.INFO, "decay-watch", token=e.token, started_at=st.spike_started_at)

        if st.spike_started_at and (now_wall - st.spike_started_at) <= DECAY_WINDOW_SECONDS:
            if unique_10s <= st.last_unique_buyers and burst_10s < (st.last_burst_weight * 0.5):
                st.blacklist_until = now_wall + BLACKLIST_SECONDS
                log_event(logger, logging.INFO, "momentum-fail", token=e.token, reason="no_follow_through", blacklist_seconds=BLACKLIST_SECONDS)
                _record_decision(
                    e,
                    stage="routing",
                    decision="momentum_fail",
                    reasons=["no_follow_through"],
                    attention_score=attention_score,
                    risk_score=risk_score,
                    confidence_score=e.confidence,
                    creator_score=float(creator_score_info.get("score") or 0.0),
                    lifecycle=str(extra.get("lifecycle") or "unknown"),
                )
                return out

        if st.spike_started_at and (now_wall - st.spike_started_at) > DECAY_WINDOW_SECONDS:
            st.spike_started_at = 0.0

        if ENGINE_MODE == "long_term":
            unique_buyers_5m = int(attn_metrics.get("unique_buyers_5m") or 0)
            if unique_buyers_5m < LONG_MIN_UNIQUE_BUYERS_5M or elite_score < LONG_MIN_ELITE_SCORE:
                log_event(
                    logger,
                    logging.INFO,
                    "engine-gate",
                    token=e.token,
                    mode="long_term",
                    unique_buyers_5m=unique_buyers_5m,
                    elite_score=elite_score,
                )
                _record_decision(
                    e,
                    stage="routing",
                    decision="engine_gate_skip",
                    reasons=["long_term_gate"],
                    attention_score=attention_score,
                    risk_score=risk_score,
                    confidence_score=e.confidence,
                    creator_score=float(creator_score_info.get("score") or 0.0),
                    lifecycle=str(extra.get("lifecycle") or "unknown"),
                )
                return out

        if ENABLE_WALLET and ts.creator:
            extra["wallet_risk"] = await score_wallet_risk(ts.creator)
            wr = extra.get("wallet_risk")
            if wr and wr.get("score", 1.0) < 0.3:
                e.confidence = bump(
                    e.confidence, CONF_WEIGHTS["wallet_low_risk"], CAPS["heating"]
                )
                e.reasons.append("wallet_low_risk")

        if ts.signals > 1:
            e.confidence = bump(e.confidence, CONF_WEIGHTS["repeat"], CAPS["heating"])
            e.reasons.append(f"repeat_{ts.signals}")

        # Persist current metrics for progression and rate-limiting
        state_init()
        prev_metrics = get_last_metrics(e.token)
        current_metrics = {
            "attention_score": attention_score,
            "unique_buyers_5m": attn_metrics.get("unique_buyers_5m") if isinstance(attn_metrics, dict) else 0,
            "liquidity": (extra.get("dex_summary") or {}).get("liquidity_usd") if isinstance(extra, dict) else 0,
            "score": e.confidence,
        }
        upsert_seen(e.token, current_metrics)
        improved, improved_keys = metrics_improved(
            prev_metrics,
            current_metrics,
            PROGRESSION_ATTENTION_DELTA,
            PROGRESSION_BUYER_DELTA,
            PROGRESSION_LIQ_DELTA,
            PROGRESSION_SCORE_DELTA,
        )
        prev_attention = float(prev_metrics.get("attention_score") or 0.0) if isinstance(prev_metrics, dict) else 0.0

        candidate_event_extra = None

        # ------------------------------------------------------------
        # Attention-driven candidate emission (Phase 1)
        # ------------------------------------------------------------
        if ENABLE_ATTENTION_CANDIDATE:
            # Candidate is an EARLY-WATCHLIST signal, not a promotion.
            # It is driven by coordination/attention, not market cap.
            attention_unavailable = False
            if attn_reasons:
                attention_unavailable = all(
                    isinstance(r, str) and r.startswith("source_unavailable")
                    for r in attn_reasons
                )
            elif attention_score is None or attention_score <= 0.0:
                attention_unavailable = True
            ok, gate_reasons, lifecycle = admission_check_candidate(
                0.0 if attention_score is None else attention_score,
                0.0 if risk_score is None else risk_score,
                extra,
                dex_summary,
                attention_unavailable,
                candidate_config,
                token_is_tradeable=token_is_tradeable,
                bonding_curve_verified=bonding_curve_verified,
            )
            if isinstance(extra.get("watch_override"), dict) and not ok:
                extra["watch_override_gate_bypass"] = list(gate_reasons)
                gate_reasons = [f"watch_override_bypass:{reason}" for reason in gate_reasons]
                ok = True
                lifecycle = lifecycle or "watch_override"
                log_event(
                    logger,
                    logging.INFO,
                    "watch-override-candidate-gate-bypass",
                    token=e.token,
                    reasons=gate_reasons,
                )
            if not ok:
                logger.info(
                    "[pre-candidate-skip] token=%s sniper_conditions_met=%s",
                    e.token,
                    sniper_conditions_met,
                )
                logger.info(
                    "[candidate-skip] token=%s reasons=%s attention=%s risk=%s",
                    e.token,
                    gate_reasons,
                    attention_score,
                    risk_score,
                )
                _record_decision(
                    e,
                    stage="candidate",
                    decision="candidate_gate_skip",
                    reasons=gate_reasons,
                    attention_score=attention_score,
                    risk_score=risk_score,
                    confidence_score=e.confidence,
                    creator_score=float(creator_score_info.get("score") or 0.0),
                    lifecycle=lifecycle,
                    policy_name=candidate_policy.get("policy_name"),
                    policy_version=candidate_policy.get("policy_version"),
                )
            else:
                if attention_unavailable:
                    logger.info("[candidate-warning] attention_unavailable token=%s", e.token)
                if not extra.get("bonding_curve") and extra.get("bonding_curve_liquidity") is None:
                    logger.info("[candidate-warning] curve_liq_unknown token=%s", e.token)
                logger.info("[candidate-lifecycle] token=%s lifecycle=%s", e.token, lifecycle)
                logger.info(
                    "[candidate-attention] token=%s attention=%s risk=%s attn_reasons=%s",
                    e.token,
                    attention_score,
                    risk_score,
                    attn_reasons,
                )
                creator_score_value = float(creator_score_info.get("score") or 0.0)
                creator_ok = creator_score_value >= creator_min
                attention_improving = (attention_score or 0.0) > prev_attention
                send_eligible, send_reasons, confirmation_signals = _candidate_send_decision(
                    attention_score=attention_score,
                    creator_score=creator_score_value,
                    extra=extra,
                    dex_summary=dex_summary,
                )
                if isinstance(extra.get("watch_override"), dict):
                    send_eligible = True
                    send_reasons = list(dict.fromkeys(["watch_override_approved", *send_reasons]))
                    confirmation_signals = list(dict.fromkeys(["watch_override", *confirmation_signals]))
                send_eligible, send_reasons, candidate_ev = _apply_candidate_ev_gate(
                    send_eligible=send_eligible,
                    send_reasons=send_reasons,
                    extra=extra,
                    dex_summary=dex_summary,
                    attention_score=attention_score,
                    risk_score=risk_score,
                )
                if send_eligible is False and any(reason.startswith("ev_gate:") for reason in send_reasons):
                    logger.info(
                        "[candidate-ev-skip] token=%s net_edge_bps=%s cost_bps=%s reasons=%s",
                        e.token,
                        candidate_ev.get("net_edge_bps"),
                        candidate_ev.get("cost_bps"),
                        candidate_ev.get("reasons") or [],
                    )
                extra["candidate_send_eligible"] = send_eligible
                extra["candidate_rate_limit_checked"] = bool(send_eligible)
                allow_rate = allow_candidate_rate_limit(max(1, EARLY_WATCH_RATE_LIMIT_PER_HOUR)) if send_eligible else True
                should_send = send_eligible and allow_rate
                extra["candidate_rate_limit_allowed"] = allow_rate
                extra["candidate_confirmation_signals"] = confirmation_signals
                extra["candidate_send_reasons"] = send_reasons
                if not send_eligible:
                    logger.info(
                        "[pre-candidate-skip] token=%s sniper_conditions_met=%s",
                        e.token,
                        sniper_conditions_met,
                    )
                    logger.info(
                        "[candidate-skip] reason=no_creator_or_improve token=%s send_reasons=%s confirmations=%s",
                        e.token,
                        send_reasons,
                        confirmation_signals,
                    )
                    _record_decision(
                        e,
                        stage="candidate",
                        decision="candidate_not_eligible",
                        action_taken="skip",
                        reasons=send_reasons or ["no_creator_or_attention"],
                        attention_score=attention_score,
                        risk_score=risk_score,
                        confidence_score=e.confidence,
                        creator_score=creator_score_value,
                        lifecycle=lifecycle,
                        policy_name=candidate_policy.get("policy_name"),
                        policy_version=candidate_policy.get("policy_version"),
                    )
                elif not allow_rate:
                    logger.info(
                        "[pre-candidate-skip] token=%s sniper_conditions_met=%s",
                        e.token,
                        sniper_conditions_met,
                    )
                    logger.info("[candidate-skip] reason=rate_limited token=%s", e.token)
                    _record_decision(
                        e,
                        stage="candidate",
                        decision="candidate_rate_limited",
                        action_taken="hold",
                        reasons=["rate_limited"],
                        attention_score=attention_score,
                        risk_score=risk_score,
                        confidence_score=e.confidence,
                        creator_score=creator_score_value,
                        lifecycle=lifecycle,
                        policy_name=candidate_policy.get("policy_name"),
                        policy_version=candidate_policy.get("policy_version"),
                    )

                extra["lifecycle"] = lifecycle
                candidate_state = get_candidate_state(e.token)
                alert_sent = bool(candidate_state.get("alert_sent"))
                message_id = candidate_state.get("message_id") or ""
                progression_ok = improved or not alert_sent
                extra["candidate_progression_ok"] = progression_ok
                should_send = should_send and progression_ok
                extra["candidate_send"] = should_send
                extra["candidate_edit"] = alert_sent and improved
                extra["candidate_improved"] = improved
                extra["candidate_improved_keys"] = improved_keys
                extra["candidate_message_id"] = message_id
                if send_eligible and allow_rate:
                    _record_decision(
                        e,
                        stage="candidate",
                        decision="candidate_ready" if should_send else "candidate_buffered",
                        action_taken="emit" if should_send else "hold",
                        reasons=improved_keys if improved_keys else [],
                        attention_score=attention_score,
                        risk_score=risk_score,
                        confidence_score=e.confidence,
                        creator_score=creator_score_value,
                        lifecycle=lifecycle,
                        policy_name=candidate_policy.get("policy_name"),
                        policy_version=candidate_policy.get("policy_version"),
                    )
                logger.info(
                    "[candidate-progress] token=%s improved=%s keys=%s confirmations=%s send_reasons=%s route_tier=%s route_blockers=%s",
                    e.token,
                    improved,
                    improved_keys,
                    confirmation_signals,
                    send_reasons,
                    str((extra.get("route_decision") if isinstance(extra.get("route_decision"), dict) else {}).get("tier") or ""),
                    (extra.get("route_decision") if isinstance(extra.get("route_decision"), dict) else {}).get("blockers") or [],
                )
                upsert_candidate_state(
                    e.token,
                    attention_score or 0.0,
                    e.confidence,
                    current_metrics.get("liquidity") or 0.0,
                    current_metrics.get("unique_buyers_5m") or 0,
                    float(creator_score_info.get("score") or 0.0),
                    lifecycle,
                )
                # Determine recheck stage
                lifecycle_policy = candidate_lifecycle_policy()
                stage = "A"
                if (
                    (attention_score or 0.0) >= lifecycle_policy.candidate_stage_b_attention_min
                    or (attn_metrics.get("unique_buyers_5m") or 0) >= lifecycle_policy.candidate_stage_b_unique_buyers_5m_min
                    or ("liquidity" in improved_keys)
                ):
                    stage = "B"
                if (
                    e.confidence >= lifecycle_policy.candidate_stage_c_confidence_min
                    or extra.get("candidate_send")
                    or float(creator_score_info.get("score") or 0.0) >= lifecycle_policy.candidate_stage_c_creator_min
                ):
                    stage = "C"

                cand_state = get_candidate_state(e.token)
                now_ts = int(time.time())
                # Stop conditions
                if cand_state:
                    flat_stop = update_stop_counters(
                        e.token,
                        float(cand_state.get("last_liquidity") or 0.0),
                        current_metrics.get("liquidity") or 0.0,
                        int(cand_state.get("last_unique_buyers") or 0),
                        int(current_metrics.get("unique_buyers_5m") or 0),
                        CAND_MIN_CURVE_LIQ_USD,
                    )
                    if flat_stop:
                        logger.info("[recheck-stop] token=%s reason=flat_metrics", e.token)
                    age_sec = now_ts - int(cand_state.get("first_seen_at") or now_ts)
                    if age_sec > lifecycle_policy.recheck_stop_max_age_days * 86400:
                        logger.info("[recheck-stop] token=%s reason=age>30d", e.token)
                    if (
                        age_sec > lifecycle_policy.recheck_stop_never_crossed_days * 86400
                        and float(cand_state.get("max_confidence") or 0.0) < lifecycle_policy.recheck_stop_never_crossed_confidence_min
                    ):
                        logger.info(
                            "[recheck-stop] token=%s reason=never_crossed_%.2f",
                            e.token,
                            lifecycle_policy.recheck_stop_never_crossed_confidence_min,
                        )

                # Schedule rechecks
                if not should_mute(e.token):
                    next_check_at = int(cand_state.get("next_check_at") or 0) if cand_state else 0
                    if next_check_at and next_check_at > now_ts:
                        pass
                    else:
                        if min_liquidity_gate(extra, CAND_MIN_CURVE_LIQ_USD):
                            async def _recheck_fn(token: str) -> None:
                                await process_event(
                                    state,
                                    Event(
                                        type="recheck",
                                        source="engine",
                                        token=token,
                                        creator=ts.creator,
                                        confidence=0.0,
                                        reasons=["scheduled_recheck"],
                                        extra=dict(extra),
                                    ),
                                )
                            e._recheck_fn = _recheck_fn  # type: ignore[attr-defined]
                            asyncio.create_task(
                                schedule_rechecks(
                                    state,
                                    e,
                                    extra,
                                    CAND_MIN_CURVE_LIQ_USD,
                                    stage,
                                )
                            )
                candidate_event_extra = dict(extra)

        attention_bonus_adjustment = 0.0
        if ENABLE_ATTENTION_BONUS and attention_score is not None and risk_score is not None:
            now = time.time()
            in_window = now - ts.first_seen_ts <= (ATTENTION_WINDOW_MINUTES * 60)
            liquidity_ok = False
            try:
                liquidity_ok = state.liquidity_stable(e.token, window_sec=900)
            except Exception:
                liquidity_ok = False

            if (
                attention_score >= ATTENTION_MIN_FOR_WINDOW
                and risk_score < RISK_VETO_THRESHOLD
                and in_window
                and liquidity_ok
            ):
                attn_bonus = min(ATTENTION_BONUS_CAP, attention_score * ATTENTION_BONUS_CAP)
                risk_penalty = risk_score * ATTENTION_BONUS_CAP
                attention_bonus_adjustment = attn_bonus - risk_penalty
                logger.info(
                    "[score-adjust] attn_bonus=%.3f risk_penalty=%.3f net=%.3f",
                    attn_bonus,
                    risk_penalty,
                    attention_bonus_adjustment,
                )

        execution_bonus_adjustment = 0.0
        if ENABLE_EXECUTION and edge_bps >= MIN_EDGE_BPS:
            execution_bonus_adjustment = min(EXECUTION_BONUS_CAP, edge_bps / 10000.0)
            logger.info(
                "[exec-bonus] edge_bps=%.1f exec_bonus=%.3f",
                edge_bps,
                execution_bonus_adjustment,
            )

        # Dynamic confidence scoring
        liq_usd = 0.0
        if isinstance(extra, dict):
            liq_usd = float((extra.get("dex_summary") or {}).get("liquidity_usd") or 0.0)
        liquidity_factor = min(liq_usd / promoted_liquidity_min, 1.0) if promoted_liquidity_min > 0 else 0.0
        creator_score = float(creator_score_info.get("score") or 0.0)
        e.confidence, confidence_components = compute_confidence_score(
            attention_score=attention_score,
            risk_score=risk_score,
            creator_score=creator_score,
            liquidity_factor=liquidity_factor,
        )
        e.confidence = max(0.0, min(1.0, e.confidence + attention_bonus_adjustment + execution_bonus_adjustment))
        extra["metric_states"]["confidence"] = metric_state(e.confidence, status="computed")
        logger.info(
            "[score-components] token=%s attention=%s risk=%s creator=%.3f liquidity_factor=%.3f bonus_adj=%.3f exec_adj=%.3f final_score=%.3f fallbacks=%s",
            e.token,
            attention_score,
            risk_score,
            creator_score,
            liquidity_factor,
            attention_bonus_adjustment,
            execution_bonus_adjustment,
            e.confidence,
            confidence_components,
        )
        ts.confidence = max(ts.confidence, e.confidence)
        logger.info("[score] computed token=%s score=%.3f", e.token, e.confidence)

        for emitted_event in out:
            if emitted_event.type == "heating_up" and emitted_event.token == e.token:
                emitted_event.confidence = e.confidence
                emitted_event.extra = dict(extra)
                emitted_event.extra["metric_states"] = dict(extra.get("metric_states") or {})

        if candidate_event_extra is not None:
            candidate_event_extra["metric_states"] = dict(extra.get("metric_states") or {})
            out.append(
                Event(
                    type="candidate",
                    source="engine",
                    token=e.token,
                    creator=ts.creator,
                    confidence=e.confidence,
                    reasons=list(e.reasons),
                    extra=candidate_event_extra,
                    signature=e.signature,
                )
            )

        # Remove legacy heating_up sends; progression is handled via candidate updates.
        lifecycle_policy = candidate_lifecycle_policy()
        if lifecycle_policy.heating_review_confidence_min <= e.confidence < lifecycle_policy.heating_review_confidence_max:
            gate_pass, gate_reasons = evaluate_alert_gate(
                "heating_up",
                extra.get("dex_summary") if isinstance(extra, dict) else None,
            )
            if not gate_pass:
                logger.info(
                    "[gate-skip] stage=heating_up token=%s reasons=%s",
                    e.token,
                    gate_reasons,
                )
                _record_decision(
                    e,
                    stage="heating_up",
                    decision="heating_gate_skip",
                    reasons=gate_reasons,
                    attention_score=attention_score,
                    risk_score=risk_score,
                    confidence_score=e.confidence,
                    creator_score=creator_score,
                    lifecycle=str(extra.get("lifecycle") or ""),
                    policy_name=promoted_policy.get("policy_name"),
                    policy_version=promoted_policy.get("policy_version"),
                )
            else:
                pass

        if e.confidence >= promoted_confidence_min and e.token and not ts.is_promoted:
            dex_summary = extra.get("dex_summary") if isinstance(extra, dict) else None
            has_dex_pool = bool(dex_summary)
            if not has_dex_pool:
                logger.info("[promotion-block] reason=no_dex_pool token=%s", e.token)
                update_promo_confirm(e.token, False)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["no_dex_pool"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="unknown", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            liq = float((dex_summary or {}).get("liquidity_usd") or 0.0)
            buyers_15m = int(attn_metrics.get("unique_buyers_15m") or 0)
            lp_drain = bool(extra.get("lp_drain"))
            creator_sell = bool(extra.get("creator_sold"))
            if liq < promoted_liquidity_min:
                logger.info("[promotion-block] reason=liq_low token=%s liq=%.0f", e.token, liq)
                update_promo_confirm(e.token, False)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["liq_low"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if buyers_15m < promoted_buyers_15m_min:
                logger.info("[promotion-block] reason=buyers_low token=%s buyers_15m=%s", e.token, buyers_15m)
                update_promo_confirm(e.token, False)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["buyers_low"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if attention_score is None or attention_score < promoted_attention_min:
                logger.info("[promotion-block] reason=attention_low token=%s attention=%s", e.token, attention_score)
                update_promo_confirm(e.token, False)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["attention_low"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if risk_score is not None and risk_score >= promoted_risk_max:
                logger.info("[promotion-block] reason=risk_high token=%s risk=%.2f", e.token, risk_score)
                update_promo_confirm(e.token, False)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["risk_high"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if lp_drain:
                logger.info("[promotion-block] reason=lp_drain token=%s", e.token)
                update_promo_confirm(e.token, False)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["lp_drain"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if creator_sell:
                logger.info("[promotion-block] reason=creator_sell token=%s", e.token)
                update_promo_confirm(e.token, False)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["creator_sell"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out

            required_confirmations, promotion_strength_reasons = promotion_confirmation_target(
                confidence_score=e.confidence,
                confidence_min=promoted_confidence_min,
                attention_score=attention_score,
                attention_min=promoted_attention_min,
                risk_score=risk_score,
                risk_max=promoted_risk_max,
                liquidity_usd=liq,
                liquidity_min=promoted_liquidity_min,
                buyers_15m=buyers_15m,
                buyers_15m_min=promoted_buyers_15m_min,
                extra=extra,
                dex_summary=dex_summary,
            )
            gate_pass, gate_reasons = evaluate_alert_gate(
                "promoted",
                dex_summary,
            )
            if not gate_pass:
                update_promo_confirm(e.token, False)
                logger.info(
                    "[gate-skip] stage=promoted token=%s reasons=%s",
                    e.token,
                    gate_reasons,
                )
                _record_decision(e, stage="promoted", decision="promotion_gate_skip", reasons=gate_reasons, attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            confirm_count = update_promo_confirm(e.token, True)
            logger.info(
                "[promotion-check] token=%s confirm_count=%s required=%s strength_reasons=%s",
                e.token,
                confirm_count,
                required_confirmations,
                promotion_strength_reasons,
            )
            if confirm_count < required_confirmations:
                _record_decision(
                    e,
                    stage="promoted",
                    decision="promotion_wait_confirm",
                    reasons=[f"confirm_count:{confirm_count}", f"required_confirmations:{required_confirmations}", *promotion_strength_reasons],
                    attention_score=attention_score,
                    risk_score=risk_score,
                    confidence_score=e.confidence,
                    creator_score=creator_score,
                    lifecycle="dex",
                    policy_name=promoted_policy.get("policy_name"),
                    policy_version=promoted_policy.get("policy_version"),
                )
                return out
            logger.info(
                "[promotion-validated] token=%s score=%.3f threshold=%.3f",
                e.token,
                e.confidence,
                promoted_confidence_min,
            )
            ts.is_promoted = True
            if isinstance(extra, dict):
                extra["lifecycle"] = "dex"
            out.append(
                Event(
                    type="promoted",
                    source="engine",
                    token=e.token,
                    creator=ts.creator,
                    confidence=e.confidence,
                    reasons=e.reasons + ["promotion_gate_passed"],
                    extra=extra,
                    signature=e.signature,
                )
            )
            _record_decision(e, stage="promoted", decision="promoted_sent", action_taken="emit", reasons=["promotion_gate_passed"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))

    return _apply_route_precedence(out)

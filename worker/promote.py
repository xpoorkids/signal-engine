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
    SNIPER_MIN_UNIQUE_10S,
    SNIPER_MIN_BURST10S,
    SNIPER_MIN_ELITE_SCORE,
    AGE_BYPASS_TTL_SECONDS,
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
)
from worker.confidence import CONF_WEIGHTS, CAPS, bump
from worker.wallet_risk import score_wallet_risk
from worker.dex import dex_enrich_token, select_best_pair, summarize_pair
from worker.forensics import analyze_risk
from worker.execution import estimate_edge
from worker.trade_validator import validate_trade
from worker.attention import compute_attention, register_buyer
from worker.token_state import _ts
from worker.elite import ELITE
from worker.metadata import fetch_token_metadata
from worker.alert_gate import evaluate_alert_gate, admission_check_candidate
from worker.creator_score import compute_creator_score
from worker.progression import metrics_improved
from worker.recheck import schedule_rechecks, update_stop_counters, min_liquidity_gate
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
from app.services.wallet_service import wallet_risk_score

logger = logging.getLogger(__name__)
logger.info("[PROMOTE FILE LOADED]")


def _candidate_send_eligible(
    attention_score: float | None,
    creator_score: float,
    creator_min: float = EARLY_CREATOR_MIN,
) -> bool:
    attn = float(attention_score or 0.0)
    # Creator quality can help borderline setups, but should not push weak attention through on its own.
    return attn >= 0.50 or (creator_score >= creator_min and attn >= 0.35)


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
    try:
        extra = e.extra if isinstance(e.extra, dict) else {}
        dex_summary = extra.get("dex_summary") if isinstance(extra.get("dex_summary"), dict) else {}
        attention_metrics = extra.get("attention_metrics") if isinstance(extra.get("attention_metrics"), dict) else {}
        features = {
            "attention_score": attention_score,
            "risk_score": risk_score,
            "confidence_score": confidence_score,
            "creator_score": creator_score,
            "lifecycle": lifecycle,
            "market_cap_usd": dex_summary.get("market_cap") or dex_summary.get("fdv"),
            "liquidity_usd": dex_summary.get("liquidity_usd"),
            "volume_m5_usd": dex_summary.get("volume_m5"),
            "age_minutes": dex_summary.get("age_minutes"),
            "price_change_m5": dex_summary.get("price_change_m5"),
            "price_change_h1": dex_summary.get("price_change_h1"),
            "txns_m5_buys": dex_summary.get("txns_m5_buys"),
            "txns_m5_sells": dex_summary.get("txns_m5_sells"),
            "unique_buyers_5m": attention_metrics.get("unique_buyers_5m"),
            "unique_buyers_15m": attention_metrics.get("unique_buyers_15m"),
            "burst_count_60s": attention_metrics.get("burst_count_60s"),
            "tracked_wallet_hits": attention_metrics.get("tracked_wallet_hits"),
            "kol_wallet_hits": attention_metrics.get("kol_wallet_hits"),
            "candidate_send_eligible": extra.get("candidate_send"),
            "candidate_edit": extra.get("candidate_edit"),
            "candidate_improved": extra.get("candidate_improved"),
            "candidate_rate_limit_allowed": extra.get("candidate_rate_limit_allowed"),
            "candidate_progression_ok": extra.get("candidate_progression_ok"),
            "has_dex_pool": bool(dex_summary),
            "lp_drain": extra.get("lp_drain"),
            "creator_sell": extra.get("creator_sold"),
        }
        features.update(classify_policy_regime(features, stage=stage, ts_value=e.ts))
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
            print(f"[blacklist-skip] token={e.token} until={st.blacklist_until}", flush=True)
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
                print(
                    f"[skip-attention] token={e.token} sol={buy_size_sol} min_sol={min_sol}",
                    flush=True,
                )
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

        extra: Dict[str, Any] = dict(e.extra)
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
        try:
            mint_auth, freeze_auth = ELITE.auth_check(e.token)
            print(
                f"[auth-check] token={e.token} mint_auth={1 if mint_auth else 0} freeze_auth={1 if freeze_auth else 0}",
                flush=True,
            )
            if mint_auth:
                print(f"[risk-gate] token={e.token} reason=mint_authority_active", flush=True)
                hard_fail = True
                hard_fail_from_authority_checks = True
            if freeze_auth:
                print(f"[risk-gate] token={e.token} reason=freeze_authority_active", flush=True)
                hard_fail = True
                hard_fail_from_authority_checks = True
        except Exception:
            mint_auth = None
            freeze_auth = None

        try:
            liq_usd, liq_locked, liq_drop = ELITE.liq_check(e.token, dex_summary)
            print(
                f"[liq-check] token={e.token} liq_usd={liq_usd} locked={1 if liq_locked else 0}",
                flush=True,
            )
            liquidity_unknown = False
            if liq_usd == 0:
                liquidity_unknown = True
            elif liq_usd < 15000:
                print(f"[risk-gate] token={e.token} reason=low_liquidity", flush=True)
                hard_fail = True
            if not liquidity_unknown:
                if liq_drop:
                    print(f"[risk-gate] token={e.token} reason=liq_drop_spike", flush=True)
                    hard_fail = True
                if liq_locked is False:
                    print(f"[risk-gate] token={e.token} reason=liq_unlocked", flush=True)
                    hard_fail = True
        except Exception:
            liq_usd = None
            liq_locked = None
            liq_drop = None
            liquidity_unknown = True


        unique_10s = 0
        burst_10s = 0
        unique_30s = 0
        top_share = 0.0
        wash_suppress = 0.0
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
            wash_suppress = 0.30 if (top_share >= 0.70 and unique_30s <= 2) else 0.0
        except Exception:
            pass

        token_wallet_risk = wallet_risk_score(e.token)
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
                    "reasons": ["validator_exception"],
                    "warnings": [],
                    "checks": [],
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
            return [e]

        if liquidity_unknown:
            if (
                ENGINE_MODE == "balanced"
                and unique_10s >= 2
                and burst_10s >= 6
                and elite_score >= 8
            ):
                print(f"[liq-unknown-bypass] token={e.token}", flush=True)
            else:
                hard_fail = True

        if hard_fail:
            return out

        age_bypassed = False
        if ENGINE_MODE == "balanced":
            if (
                elite_score >= SNIPER_MIN_ELITE_SCORE
                and unique_10s >= SNIPER_MIN_UNIQUE_10S
                and burst_10s >= SNIPER_MIN_BURST10S
            ):
                st.age_bypass_until = now_wall + AGE_BYPASS_TTL_SECONDS
                print(
                    f"[age-bypass] token={e.token} elite={elite_score} unique_10s={unique_10s} burst10s={burst_10s} ttl={AGE_BYPASS_TTL_SECONDS}",
                    flush=True,
                )
            age_bypassed = now_wall <= st.age_bypass_until
        elif ENGINE_MODE == "long_term":
            st.age_bypass_until = 0.0
            age_bypassed = False
        extra["age_bypass_until"] = st.age_bypass_until if age_bypassed else 0.0

        sniper_conditions_met = (
            ENGINE_MODE == "balanced"
            and unique_10s >= SNIPER_MIN_UNIQUE_10S
            and burst_10s >= 6
            and elite_score >= 8
            and not hard_fail_from_authority_checks
        )
        if sniper_conditions_met:
            if now_wall > st.age_bypass_until:
                st.age_bypass_until = now_wall + AGE_BYPASS_TTL_SECONDS
                extra["age_bypass_until"] = st.age_bypass_until
            print(
                f"[discord-sniper] token={e.token} elite={elite_score} unique_10s={unique_10s} burst10s={burst_10s}",
                flush=True,
            )
            print(
                f"[discord-send-attempt] tier=sniper url_set={1 if bool(DISCORD_WEBHOOK_URL) else 0} token={e.token}",
                flush=True,
            )
            out.append(
                Event(
                    type="heating_up",
                    source="engine",
                    token=e.token,
                    creator=ts.creator,
                    confidence=e.confidence,
                    reasons=e.reasons + ["sniper_route"],
                    extra=dict(extra),
                    signature=e.signature,
                )
            )

        accel_boost = 0.0
        if unique_10s == 3:
            accel_boost = 0.10
        elif unique_10s == 4:
            accel_boost = 0.15
        elif unique_10s >= 5:
            accel_boost = 0.20

        if elite_score >= SNIPER_MIN_ELITE_SCORE or accel_boost >= 0.10:
            if st.spike_started_at == 0:
                st.spike_started_at = now_wall
                st.last_unique_buyers = unique_10s
                st.last_burst_weight = burst_10s
                print(f"[decay-watch] token={e.token} started_at={st.spike_started_at}", flush=True)

        if st.spike_started_at and (now_wall - st.spike_started_at) <= DECAY_WINDOW_SECONDS:
            if unique_10s <= st.last_unique_buyers and burst_10s < (st.last_burst_weight * 0.5):
                st.blacklist_until = now_wall + BLACKLIST_SECONDS
                print(
                    f"[momentum-fail] token={e.token} reason=no_follow_through blacklist={BLACKLIST_SECONDS}",
                    flush=True,
                )
                return out

        if st.spike_started_at and (now_wall - st.spike_started_at) > DECAY_WINDOW_SECONDS:
            st.spike_started_at = 0.0

        if ENGINE_MODE == "long_term":
            unique_buyers_5m = int(attn_metrics.get("unique_buyers_5m") or 0)
            if unique_buyers_5m < LONG_MIN_UNIQUE_BUYERS_5M or elite_score < LONG_MIN_ELITE_SCORE:
                print(
                    f"[engine-gate] token={e.token} mode=long_term unique_buyers_5m={unique_buyers_5m} elite={elite_score}",
                    flush=True,
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
                send_eligible = _candidate_send_eligible(attention_score, creator_score_value, creator_min)
                allow_rate = allow_candidate_rate_limit(EARLY_WATCH_RATE_LIMIT_PER_HOUR) if send_eligible else False
                should_send = send_eligible and allow_rate
                extra["candidate_rate_limit_allowed"] = allow_rate
                if not allow_rate:
                    if send_eligible:
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
                            reasons=["rate_limited"],
                            attention_score=attention_score,
                            risk_score=risk_score,
                            confidence_score=e.confidence,
                            creator_score=creator_score_value,
                            lifecycle=lifecycle,
                            policy_name=candidate_policy.get("policy_name"),
                            policy_version=candidate_policy.get("policy_version"),
                        )
                elif not send_eligible:
                    logger.info(
                        "[pre-candidate-skip] token=%s sniper_conditions_met=%s",
                        e.token,
                        sniper_conditions_met,
                    )
                    logger.info("[candidate-skip] reason=no_creator_or_improve token=%s", e.token)
                    _record_decision(
                        e,
                        stage="candidate",
                        decision="candidate_not_eligible",
                        reasons=["no_creator_or_attention"],
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
                    "[candidate-progress] token=%s improved=%s keys=%s",
                    e.token,
                    improved,
                    improved_keys,
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
                stage = "A"
                if (
                    (attention_score or 0.0) >= 0.25
                    or (attn_metrics.get("unique_buyers_5m") or 0) >= 5
                    or ("liquidity" in improved_keys)
                ):
                    stage = "B"
                if (
                    e.confidence >= 0.50
                    or extra.get("candidate_send")
                    or float(creator_score_info.get("score") or 0.0) >= 0.5
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
                    if age_sec > 30 * 86400:
                        logger.info("[recheck-stop] token=%s reason=age>30d", e.token)
                    if age_sec > 7 * 86400 and float(cand_state.get("max_confidence") or 0.0) < 0.40:
                        logger.info("[recheck-stop] token=%s reason=never_crossed_0.40", e.token)

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
        if 0.55 <= e.confidence < 0.80:
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
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["no_dex_pool"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="unknown", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            liq = float((dex_summary or {}).get("liquidity_usd") or 0.0)
            buyers_15m = int(attn_metrics.get("unique_buyers_15m") or 0)
            lp_drain = bool(extra.get("lp_drain"))
            creator_sell = bool(extra.get("creator_sold"))
            if liq < promoted_liquidity_min:
                logger.info("[promotion-block] reason=liq_low token=%s liq=%.0f", e.token, liq)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["liq_low"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if buyers_15m < promoted_buyers_15m_min:
                logger.info("[promotion-block] reason=buyers_low token=%s buyers_15m=%s", e.token, buyers_15m)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["buyers_low"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if attention_score is None or attention_score < promoted_attention_min:
                logger.info("[promotion-block] reason=attention_low token=%s attention=%s", e.token, attention_score)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["attention_low"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if risk_score is not None and risk_score >= promoted_risk_max:
                logger.info("[promotion-block] reason=risk_high token=%s risk=%.2f", e.token, risk_score)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["risk_high"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if lp_drain:
                logger.info("[promotion-block] reason=lp_drain token=%s", e.token)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["lp_drain"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            if creator_sell:
                logger.info("[promotion-block] reason=creator_sell token=%s", e.token)
                _record_decision(e, stage="promoted", decision="promotion_block", reasons=["creator_sell"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out

            confirm_count = update_promo_confirm(e.token, True)
            logger.info(
                "[promotion-check] token=%s confirm_count=%s",
                e.token,
                confirm_count,
            )
            if confirm_count < 2:
                _record_decision(e, stage="promoted", decision="promotion_wait_confirm", reasons=[f"confirm_count:{confirm_count}"], attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
                return out
            gate_pass, gate_reasons = evaluate_alert_gate(
                "promoted",
                dex_summary,
            )
            if not gate_pass:
                logger.info(
                    "[gate-skip] stage=promoted token=%s reasons=%s",
                    e.token,
                    gate_reasons,
                )
                _record_decision(e, stage="promoted", decision="promotion_gate_skip", reasons=gate_reasons, attention_score=attention_score, risk_score=risk_score, confidence_score=e.confidence, creator_score=creator_score, lifecycle="dex", policy_name=promoted_policy.get("policy_name"), policy_version=promoted_policy.get("policy_version"))
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

    return out

from typing import Dict, Any
import logging
import time
import asyncio
from worker.events import Event
from worker.state import EngineState, bump_token
from worker.config import (
    ENABLE_DEX,
    ENABLE_WALLET,
    ENABLE_FORENSICS,
    ENABLE_ATTENTION,
    ENABLE_EXECUTION,
    ENABLE_RISK_VETO,
    ENABLE_ATTENTION_BONUS,
    ENABLE_ATTENTION_CANDIDATE,
    RISK_VETO_THRESHOLD,
    ATTENTION_BONUS_CAP,
    ATTENTION_MIN_FOR_WINDOW,
    ATTENTION_WINDOW_MINUTES,
    ATTENTION_CANDIDATE_THRESHOLD,
    EXECUTION_BONUS_CAP,
    MIN_EDGE_BPS,
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
from worker.attention import compute_attention, register_buyer
from worker.metadata import fetch_token_metadata
from worker.alert_gate import evaluate_alert_gate, admission_check_candidate
from worker.creator_score import compute_creator_score
from worker.progression import metrics_improved
from worker.recheck import schedule_rechecks, update_stop_counters, min_liquidity_gate
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

logger = logging.getLogger(__name__)
logger.info("[PROMOTE FILE LOADED]")

async def process_event(state: EngineState, e: Event) -> list[Event]:
    out: list[Event] = []
    logger.info("[PROMOTE HANDLER CALLED] type=%s token=%s", e.type, e.token)
    logger.info("[promote-enter] token=%s", e.token)

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
        if e.creator and e.type == "token_resolved" and ts.signals == 1:
            record_creator_deploy(e.creator)

        risk_score = 0.0
        risk_reasons: list[str] = []
        risk_flags: Dict[str, bool] = {}
        if ENABLE_FORENSICS:
            risk_score, risk_reasons, risk_flags = analyze_risk(e, state)
            logger.info(
                "[forensics] token=%s risk=%.2f reasons=%s flags=%s",
                e.token,
                risk_score,
                risk_reasons,
                risk_flags,
            )
            e.extra["risk_score"] = risk_score
            e.extra["risk_reasons"] = risk_reasons
            e.extra["risk_flags"] = risk_flags

        creator_score_info = {"score": 0.0, "reasons": ["creator_unknown"], "stats": {}}
        if e.creator:
            creator_score_info = compute_creator_score(e.creator)
            score_val = float(creator_score_info.get("score") or 0.0)
            risk_score = max(0.0, risk_score - (score_val * CREATOR_RISK_WEIGHT))
            e.extra["creator_score"] = score_val
            e.extra["creator_reasons"] = creator_score_info.get("reasons") or []
            e.extra["creator_stats"] = creator_score_info.get("stats") or {}
            e.extra["risk_score"] = risk_score
            logger.info(
                "[creator-score] token=%s creator=%s score=%.2f reasons=%s stats=%s",
                e.token,
                e.creator,
                score_val,
                e.extra["creator_reasons"],
                e.extra["creator_stats"],
            )

        buyer = e.extra.get("buyer") if isinstance(e.extra, dict) else None
        if buyer:
            delta_raw = None
            decimals = None
            if isinstance(e.extra, dict):
                delta_raw = e.extra.get("delta_raw")
                decimals = e.extra.get("decimals")
            delta = None
            try:
                if delta_raw is not None:
                    delta = float(delta_raw) / (10 ** int(decimals or 0))
            except Exception:
                delta = None
            weight = register_buyer(e.token, buyer, delta)
            state.record_buyer(e.token, buyer, ts=e.ts, weight=weight)

        attention_score = 0.0
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

        if ENABLE_RISK_VETO and risk_score >= RISK_VETO_THRESHOLD:
            logger.info(
                "[promote-skip] reason=risk_veto token=%s risk=%.2f threshold=%.2f",
                e.token,
                risk_score,
                RISK_VETO_THRESHOLD,
            )
            return [e]

        if e.type == "token_resolved":
            e.confidence = bump(e.confidence, CONF_WEIGHTS["token_resolved"], CAPS["heating"])
            e.reasons.append("token_resolved")

        extra: Dict[str, Any] = dict(e.extra)
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
            elif attention_score <= 0.0:
                attention_unavailable = True
            ok, gate_reasons, lifecycle = admission_check_candidate(
                attention_score,
                risk_score,
                extra,
                dex_summary,
                attention_unavailable,
            )
            if not ok:
                logger.info(
                    "[candidate-skip] token=%s reasons=%s attention=%.2f risk=%.2f",
                    e.token,
                    gate_reasons,
                    attention_score,
                    risk_score,
                )
            else:
                if attention_unavailable:
                    logger.info("[candidate-warning] attention_unavailable token=%s", e.token)
                if not extra.get("bonding_curve") and extra.get("bonding_curve_liquidity") is None:
                    logger.info("[candidate-warning] curve_liq_unknown token=%s", e.token)
                logger.info("[candidate-lifecycle] token=%s lifecycle=%s", e.token, lifecycle)
                logger.info(
                    "[candidate-attention] token=%s attention=%.2f risk=%.2f attn_reasons=%s",
                    e.token,
                    attention_score,
                    risk_score,
                    attn_reasons,
                )
                creator_ok = float(creator_score_info.get("score") or 0.0) >= EARLY_CREATOR_MIN
                attention_improving = attention_score > prev_attention
                send_eligible = creator_ok or attention_score >= 0.50
                allow_rate = allow_candidate_rate_limit(EARLY_WATCH_RATE_LIMIT_PER_HOUR) if send_eligible else False
                should_send = send_eligible and allow_rate
                if not allow_rate:
                    if send_eligible:
                        logger.info("[candidate-skip] reason=rate_limited token=%s", e.token)
                elif not send_eligible:
                    logger.info("[candidate-skip] reason=no_creator_or_improve token=%s", e.token)

                extra["lifecycle"] = lifecycle
                candidate_state = get_candidate_state(e.token)
                alert_sent = bool(candidate_state.get("alert_sent"))
                message_id = candidate_state.get("message_id") or ""
                progression_ok = improved or not alert_sent
                should_send = should_send and progression_ok
                extra["candidate_send"] = should_send
                extra["candidate_edit"] = alert_sent and improved
                extra["candidate_improved"] = improved
                extra["candidate_improved_keys"] = improved_keys
                extra["candidate_message_id"] = message_id
                logger.info(
                    "[candidate-progress] token=%s improved=%s keys=%s",
                    e.token,
                    improved,
                    improved_keys,
                )
                upsert_candidate_state(
                    e.token,
                    attention_score,
                    e.confidence,
                    current_metrics.get("liquidity") or 0.0,
                    current_metrics.get("unique_buyers_5m") or 0,
                    float(creator_score_info.get("score") or 0.0),
                    lifecycle,
                )
                # Determine recheck stage
                stage = "A"
                if (
                    attention_score >= 0.25
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
                out.append(
                    Event(
                        type="candidate",
                        source="engine",
                        token=e.token,
                        extra=dict(extra),
                    )
                )

        if ENABLE_ATTENTION_BONUS:
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
                base_confidence = e.confidence
                attn_bonus = min(ATTENTION_BONUS_CAP, attention_score * ATTENTION_BONUS_CAP)
                risk_penalty = risk_score * ATTENTION_BONUS_CAP
                e.confidence = base_confidence + attn_bonus - risk_penalty
                logger.info(
                    "[score-adjust] base=%.3f attn_bonus=%.3f risk_penalty=%.3f final=%.3f",
                    base_confidence,
                    attn_bonus,
                    risk_penalty,
                    e.confidence,
                )

        if ENABLE_EXECUTION and edge_bps >= MIN_EDGE_BPS:
            exec_bonus = min(EXECUTION_BONUS_CAP, edge_bps / 10000.0)
            e.confidence += exec_bonus
            logger.info(
                "[exec-bonus] edge_bps=%.1f exec_bonus=%.3f",
                edge_bps,
                exec_bonus,
            )

        # Dynamic confidence scoring
        liq_usd = 0.0
        if isinstance(extra, dict):
            liq_usd = float((extra.get("dex_summary") or {}).get("liquidity_usd") or 0.0)
        liquidity_factor = min(liq_usd / PROM_MIN_LIQ_USD, 1.0) if PROM_MIN_LIQ_USD > 0 else 0.0
        creator_score = float(creator_score_info.get("score") or 0.0)
        e.confidence = (
            (attention_score * 0.40)
            + ((1.0 - risk_score) * 0.30)
            + (creator_score * 0.20)
            + (liquidity_factor * 0.10)
        )
        e.confidence = max(0.0, min(1.0, e.confidence))
        logger.info(
            "[score-components] token=%s attention=%.3f risk=%.3f creator=%.3f liquidity_factor=%.3f final_score=%.3f",
            e.token,
            attention_score,
            risk_score,
            creator_score,
            liquidity_factor,
            e.confidence,
        )
        ts.confidence = max(ts.confidence, e.confidence)
        logger.info("[score] computed token=%s score=%.3f", e.token, e.confidence)

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
            else:
                pass

        if e.confidence >= 0.80 and e.token and not ts.is_promoted:
            dex_summary = extra.get("dex_summary") if isinstance(extra, dict) else None
            has_dex_pool = bool(dex_summary)
            if not has_dex_pool:
                logger.info("[promotion-block] reason=no_dex_pool token=%s", e.token)
                return out
            liq = float((dex_summary or {}).get("liquidity_usd") or 0.0)
            buyers_15m = int(attn_metrics.get("unique_buyers_15m") or 0)
            lp_drain = bool(extra.get("lp_drain"))
            creator_sell = bool(extra.get("creator_sold"))
            if liq < PROM_MIN_LIQ_USD:
                logger.info("[promotion-block] reason=liq_low token=%s liq=%.0f", e.token, liq)
                return out
            if buyers_15m < 30:
                logger.info("[promotion-block] reason=buyers_low token=%s buyers_15m=%s", e.token, buyers_15m)
                return out
            if attention_score < PROMOTION_MIN_ATTENTION:
                logger.info("[promotion-block] reason=attention_low token=%s attention=%.2f", e.token, attention_score)
                return out
            if risk_score >= PROMOTION_MAX_RISK:
                logger.info("[promotion-block] reason=risk_high token=%s risk=%.2f", e.token, risk_score)
                return out
            if lp_drain:
                logger.info("[promotion-block] reason=lp_drain token=%s", e.token)
                return out
            if creator_sell:
                logger.info("[promotion-block] reason=creator_sell token=%s", e.token)
                return out

            confirm_count = update_promo_confirm(e.token, True)
            logger.info(
                "[promotion-check] token=%s confirm_count=%s",
                e.token,
                confirm_count,
            )
            if confirm_count < 2:
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
                return out
            logger.info(
                "[promotion-validated] token=%s score=%.3f threshold=%.3f",
                e.token,
                e.confidence,
                0.80,
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

    return out

from typing import Dict, Any
import logging
import time
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
)
from worker.confidence import CONF_WEIGHTS, CAPS, bump
from worker.wallet_risk import score_wallet_risk
from worker.dex import dex_enrich_token, select_best_pair, summarize_pair
from worker.forensics import analyze_risk
from worker.execution import estimate_edge
from worker.attention import compute_attention
from worker.alert_gate import evaluate_alert_gate

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

        # ------------------------------------------------------------
        # Attention-driven candidate emission (Phase 1)
        # ------------------------------------------------------------
        if ENABLE_ATTENTION_CANDIDATE:
            # Candidate is an EARLY-WATCHLIST signal, not a promotion.
            # It is driven by coordination/attention, not market cap.
            if (
                attention_score >= ATTENTION_CANDIDATE_THRESHOLD
                and risk_score < RISK_VETO_THRESHOLD
                and state.has_basic_liquidity(e.token)
            ):
                logger.info(
                    "[candidate-attention] token=%s attention=%.2f risk=%.2f attn_reasons=%s",
                    e.token,
                    attention_score,
                    risk_score,
                    attn_reasons,
                )

                out.append(
                    Event(
                        type="candidate",
                        source="engine",
                        token=e.token,
                        extra=dict(e.extra),
                    )
                )

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

        ts.confidence = max(ts.confidence, e.confidence)
        logger.info("[score] computed token=%s score=%.3f", e.token, e.confidence)

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
                out.append(
                    Event(
                        type="heating_up",
                        source=e.source,
                        token=e.token,
                        creator=ts.creator,
                        confidence=e.confidence,
                        reasons=e.reasons,
                        extra=extra,
                        signature=e.signature,
                    )
                )

        if e.confidence >= 0.80 and e.token and not ts.is_promoted:
            gate_pass, gate_reasons = evaluate_alert_gate(
                "promoted",
                extra.get("dex_summary") if isinstance(extra, dict) else None,
            )
            if not gate_pass:
                logger.info(
                    "[gate-skip] stage=promoted token=%s reasons=%s",
                    e.token,
                    gate_reasons,
                )
                return out
            logger.info(
                "[promoted] token=%s score=%.3f threshold=%.3f",
                e.token,
                e.confidence,
                0.80,
            )
            ts.is_promoted = True
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

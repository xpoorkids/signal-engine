from typing import Dict, Any
import logging
from worker.events import Event
from worker.state import EngineState, bump_token
from worker.config import ENABLE_DEX, ENABLE_WALLET
from worker.confidence import CONF_WEIGHTS, CAPS, bump
from worker.wallet_risk import score_wallet_risk
from worker.dex import dex_enrich_token
from worker.forensics import analyze_risk
from worker.execution import estimate_edge

logger = logging.getLogger(__name__)

async def process_event(state: EngineState, e: Event) -> list[Event]:
    out: list[Event] = []
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

        # compute forensics
        risk_score, risk_reasons, risk_flags = analyze_risk(e, state)
        logger.info(
            "[forensics] token=%s risk=%.2f reasons=%s flags=%s",
            e.token,
            risk_score,
            risk_reasons,
            risk_flags,
        )

        # compute execution edge (currently stub)
        edge_bps, edge_reasons, size_cap_usd = estimate_edge(e, state)
        logger.info(
            "[execution] token=%s edge_bps=%.1f size_cap_usd=%.0f reasons=%s",
            e.token,
            edge_bps,
            size_cap_usd,
            edge_reasons,
        )

        # attach to event for future use
        e.extra["risk_score"] = risk_score
        e.extra["risk_reasons"] = risk_reasons
        e.extra["risk_flags"] = risk_flags
        e.extra["edge_bps"] = edge_bps
        e.extra["edge_reasons"] = edge_reasons
        e.extra["size_cap_usd"] = size_cap_usd

        if e.type == "token_resolved":
            e.confidence = bump(e.confidence, CONF_WEIGHTS["token_resolved"], CAPS["heating"])
            e.reasons.append("token_resolved")

        extra: Dict[str, Any] = dict(e.extra)
        if ENABLE_DEX:
            extra["dex"] = await dex_enrich_token(e.token)
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

        ts.confidence = max(ts.confidence, e.confidence)
        logger.info("[score] computed token=%s score=%.3f", e.token, e.confidence)

        if 0.55 <= e.confidence < 0.80:
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

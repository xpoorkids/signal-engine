from __future__ import annotations

from typing import Any

from app.services.signal_learning_service import get_diagnostics_summary
from worker import config as cfg


def _proposal(
    *,
    reason: str,
    action: str,
    config_key: str,
    current_value: Any,
    proposed_value: Any,
    confidence: str,
    rationale: str,
    sample_size: int,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "action": action,
        "config_key": config_key,
        "current_value": current_value,
        "proposed_value": proposed_value,
        "confidence": confidence,
        "sample_size": sample_size,
        "rationale": rationale,
    }


def build_tuning_proposals(hours: int = 72) -> dict[str, Any]:
    summary = get_diagnostics_summary(hours=max(1, hours))
    guidance = summary.get("threshold_guidance") if isinstance(summary.get("threshold_guidance"), list) else []
    proposals: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    current_map: dict[str, tuple[str, Any]] = {
        "attention<0.20": ("EARLY_ATTENTION_MIN", float(cfg.EARLY_ATTENTION_MIN)),
        "buyers_low": ("PROMOTION_MIN_ATTENTION", float(cfg.PROMOTION_MIN_ATTENTION)),
        "dex_gate:liq<12000.0": ("PROM_MIN_LIQ_USD", float(cfg.PROM_MIN_LIQ_USD)),
        "risk_high": ("PROMOTION_MAX_RISK", float(cfg.PROMOTION_MAX_RISK)),
        "age<30s": ("CAND_MIN_TOKEN_AGE_SEC", int(cfg.CAND_MIN_TOKEN_AGE_SEC)),
    }

    for item in guidance:
        reason = str(item.get("reason") or "")
        action = str(item.get("action") or "hold")
        confidence = str(item.get("confidence") or "low")
        sample_size = int(item.get("sample_size") or 0)
        if action not in {"relax_slightly", "tighten"}:
            deferred.append(
                {
                    "reason": reason,
                    "action": action,
                    "confidence": confidence,
                    "sample_size": sample_size,
                    "rationale": str(item.get("rationale") or ""),
                }
            )
            continue
        if reason not in current_map:
            deferred.append(
                {
                    "reason": reason,
                    "action": action,
                    "confidence": confidence,
                    "sample_size": sample_size,
                    "rationale": "No explicit config mapping exists for this blocker yet.",
                }
            )
            continue

        config_key, current_value = current_map[reason]
        proposed_value = current_value
        if config_key == "EARLY_ATTENTION_MIN":
            delta = 0.02
            proposed_value = round(max(0.05, current_value - delta), 2) if action == "relax_slightly" else round(min(0.5, current_value + delta), 2)
        elif config_key == "PROMOTION_MIN_ATTENTION":
            delta = 0.03
            proposed_value = round(max(0.25, current_value - delta), 2) if action == "relax_slightly" else round(min(0.9, current_value + delta), 2)
        elif config_key == "PROM_MIN_LIQ_USD":
            delta = 2000.0
            proposed_value = max(2000.0, current_value - delta) if action == "relax_slightly" else current_value + delta
        elif config_key == "PROMOTION_MAX_RISK":
            delta = 0.03
            proposed_value = round(min(0.9, current_value + delta), 2) if action == "relax_slightly" else round(max(0.2, current_value - delta), 2)
        elif config_key == "CAND_MIN_TOKEN_AGE_SEC":
            delta = 5
            proposed_value = max(5, current_value - delta) if action == "relax_slightly" else current_value + delta

        proposals.append(
            _proposal(
                reason=reason,
                action=action,
                config_key=config_key,
                current_value=current_value,
                proposed_value=proposed_value,
                confidence=confidence,
                sample_size=sample_size,
                rationale=str(item.get("rationale") or ""),
            )
        )

    proposals.sort(
        key=lambda item: (
            {"tighten": 0, "relax_slightly": 1}.get(str(item["action"]), 2),
            {"high": 0, "medium": 1, "low": 2}.get(str(item["confidence"]), 3),
            -int(item["sample_size"]),
            str(item["config_key"]),
        )
    )

    preset_overrides = {
        "strict": {},
        "balanced": {},
        "aggressive": {},
    }
    for item in proposals:
        key = str(item["config_key"])
        proposed = item["proposed_value"]
        current = item["current_value"]
        action = str(item["action"])
        if action == "tighten":
            preset_overrides["strict"][key] = proposed
            preset_overrides["balanced"].setdefault(key, current)
        elif action == "relax_slightly":
            preset_overrides["aggressive"][key] = proposed
            preset_overrides["balanced"].setdefault(key, current)

    return {
        "lookback_hours": hours,
        "generated_from": "diagnostics.threshold_guidance",
        "proposal_count": len(proposals),
        "proposals": proposals,
        "deferred": deferred[:10],
        "preset_overrides": preset_overrides,
    }

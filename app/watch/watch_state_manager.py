from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, TypedDict

from .classifier import classify_watch_stage
from .stage_state import persist_stage_state
from .stages import StageDecision, WatchStage

class WatchStageState(TypedDict):
    stage: WatchStage
    score: int
    entered_at: datetime

# Process-local, in-memory state only (ephemeral; resets on restart).
_STATE: Dict[str, WatchStageState] = {}

def _token_key(signals: Dict[str, Any]) -> Optional[str]:
    for key in ("token", "symbol", "address", "mint"):
        value = signals.get(key)
        if isinstance(value, str):
            token = value.strip()
            if token:
                return token
    return None

def evolve_watch_stage(signals: Dict[str, Any]) -> StageDecision:
    """
    Single entry point for watch stage evolution.
    Calls the classifier, evaluates transitions, and persists stage state.
    """
    decision = classify_watch_stage(signals)
    token = _token_key(signals)
    if not token:
        return decision

    chain = signals.get("chain") if isinstance(signals.get("chain"), str) else "sol"
    _STATE.get(token)
    persist_stage_state(token, chain, decision.stage, decision.score, decision.reasons)

    _STATE[token] = {
        "stage": decision.stage,
        "score": decision.score,
        "entered_at": datetime.now(timezone.utc),
    }
    return decision

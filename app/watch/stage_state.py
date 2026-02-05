from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, TypedDict

from .stages import WatchStage
from .stage_transitions import record_stage_transition

class _StageState(TypedDict):
    stage: WatchStage
    entered_at: datetime

_STATE: Dict[str, _StageState] = {}

def persist_stage_state(
    token: str,
    chain: str,
    stage: WatchStage,
    score: int,
    reasons: List[str],
) -> None:
    prev = _STATE.get(token)
    if prev is None or prev["stage"] != stage:
        record_stage_transition(token, chain, stage, score, reasons)

    _STATE[token] = {
        "stage": stage,
        "entered_at": datetime.now(timezone.utc),
    }

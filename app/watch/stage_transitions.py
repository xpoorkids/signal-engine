from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, TypedDict

from .stages import WatchStage
from app.services.watch_store import append_watch_event

class _TransitionState(TypedDict):
    stage: WatchStage
    entered_at: datetime

_STATE: Dict[str, _TransitionState] = {}

def record_stage_transition(
    token: str,
    chain: str,
    stage: WatchStage,
    score: int,
    reasons: List[str],
) -> None:
    now = datetime.now(timezone.utc)
    prev = _STATE.get(token)
    if prev and prev["stage"] == stage:
        return

    if prev:
        entered_at = prev["entered_at"]
        exited_at = now
        duration_seconds = int((exited_at - entered_at).total_seconds())
        append_watch_event({
            "token": token,
            "chain": chain,
            "from_stage": prev["stage"],
            "to_stage": stage,
            "score": score,
            "reasons": reasons,
            "entered_at": entered_at.isoformat(),
            "exited_at": exited_at.isoformat(),
            "duration_seconds": duration_seconds,
        })

    _STATE[token] = {"stage": stage, "entered_at": now}

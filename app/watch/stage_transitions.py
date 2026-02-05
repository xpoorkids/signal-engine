from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, TypedDict

import requests

from .stages import WatchStage
from app.services.watch_store import append_watch_event


class _TransitionState(TypedDict):
    stage: WatchStage
    entered_at: datetime


# Process-local, ephemeral state.
# This provides deterministic behavior within a single process only.
_STATE: Dict[str, _TransitionState] = {}


N8N_WEBHOOK_URL = "https://justsomekids.app.n8n.cloud/webhook/near-pass-transition"


def emit_stage_transition_to_n8n(event: dict) -> None:
    """
    Best-effort notification to n8n.
    Failures must never impact scoring or persistence.
    """
    try:
        requests.post(
            N8N_WEBHOOK_URL,
            json=event,
            timeout=3,
        )
    except Exception:
        # Alerts must never break scoring
        pass


def record_stage_transition(
    token: str,
    chain: str,
    stage: WatchStage,
    score: int,
    reasons: List[str],
) -> None:
    """
    Record a stage transition if and only if the stage has changed.
    Emits an append-only log event and notifies n8n.
    """
    now = datetime.now(timezone.utc)

    prev = _STATE.get(token)

    # No transition if stage is unchanged
    if prev and prev["stage"] == stage:
        return

    # Compute duration in previous stage (if known)
    duration_seconds: int | None = None
    if prev:
        duration_seconds = int((now - prev["entered_at"]).total_seconds())

    event = {
        "event": "stage_transition",
        "token": token,
        "chain": chain,
        "from_stage": prev["stage"] if prev else None,
        "to_stage": stage,
        "score": score,
        "reasons": reasons,
        "entered_at": prev["entered_at"].isoformat() if prev else None,
        "exited_at": now.isoformat(),
        "duration_seconds": duration_seconds,
        "timestamp": now.isoformat(),
    }

    # 1️⃣ Persist first (source of truth)

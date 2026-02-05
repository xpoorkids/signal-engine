from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, TypedDict

import requests

from .stages import WatchStage
from app.services.watch_store import append_watch_event

_N8N_NEAR_PASS_TRANSITION_URL = "https://justsomekids.app.n8n.cloud/webhook/near-pass-transition"
_N8N_NEAR_PASS_DEMOTION_URL = "https://justsomekids.app.n8n.cloud/webhook/near-pass-demotion"


class _TransitionState(TypedDict):
    stage: WatchStage
    entered_at: datetime


_STATE: Dict[str, _TransitionState] = {}


def _post_webhook(url: str, event: Dict[str, object]) -> None:
    try:
        requests.post(url, json=event, timeout=5)
    except Exception:
        pass


def _emit_transition_webhook(event: Dict[str, object]) -> None:
    url = None
    if event.get("to_stage") == "near_pass":
        url = _N8N_NEAR_PASS_TRANSITION_URL
    elif event.get("from_stage") == "near_pass" and event.get("to_stage") != "near_pass":
        url = _N8N_NEAR_PASS_DEMOTION_URL

    if not url:
        return

    _post_webhook(url, event)


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

    duration_seconds: int | None = None
    if prev:
        entered_at = prev["entered_at"]
        exited_at = now
        duration_seconds = int((exited_at - entered_at).total_seconds())

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

    append_watch_event(event)
    _emit_transition_webhook(event)

    _STATE[token] = {
        "stage": stage,
        "entered_at": now,
    }

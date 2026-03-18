from __future__ import annotations

from dataclasses import asdict, dataclass
import time
import uuid

from worker.config import (
    SHADOW_SUBMISSION_ACK_DELAY_MS,
    SHADOW_SUBMISSION_LANDING_DELAY_MS,
    SHADOW_SUBMISSION_MAX_LANDING_WAIT_MS,
)


@dataclass(frozen=True)
class SubmissionSimulation:
    request_id: str
    status: str
    requested_ts: int
    ack_ts: int
    landing_ts: int
    expires_ts: int
    terminal: bool
    reason: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def create_submission_plan(*, now_ts: int | None = None) -> SubmissionSimulation:
    now_ms = int((now_ts if now_ts is not None else time.time()) * 1000)
    ack_ts = now_ms + max(0, SHADOW_SUBMISSION_ACK_DELAY_MS)
    landing_ts = ack_ts + max(0, SHADOW_SUBMISSION_LANDING_DELAY_MS)
    expires_ts = now_ms + max(landing_ts - now_ms, SHADOW_SUBMISSION_MAX_LANDING_WAIT_MS)
    return SubmissionSimulation(
        request_id=uuid.uuid4().hex,
        status="submit_requested",
        requested_ts=now_ms,
        ack_ts=ack_ts,
        landing_ts=landing_ts,
        expires_ts=expires_ts,
        terminal=False,
    )


def advance_submission_plan(plan: dict | None, *, now_ts: int | None = None, quote_expires_ts: int | None = None) -> SubmissionSimulation:
    data = plan if isinstance(plan, dict) else {}
    now_ms = int((now_ts if now_ts is not None else time.time()) * 1000)
    requested_ts = int(data.get("requested_ts") or now_ms)
    ack_ts = int(data.get("ack_ts") or requested_ts)
    landing_ts = int(data.get("landing_ts") or ack_ts)
    expires_ts = int(data.get("expires_ts") or (requested_ts + SHADOW_SUBMISSION_MAX_LANDING_WAIT_MS))
    request_id = str(data.get("request_id") or uuid.uuid4().hex)
    effective_quote_expiry_ms = int(quote_expires_ts * 1000) if quote_expires_ts else None
    if effective_quote_expiry_ms and now_ms > effective_quote_expiry_ms:
        return SubmissionSimulation(
            request_id=request_id,
            status="submit_expired",
            requested_ts=requested_ts,
            ack_ts=ack_ts,
            landing_ts=landing_ts,
            expires_ts=expires_ts,
            terminal=True,
            reason="quote_expired",
        )
    if now_ms > expires_ts:
        return SubmissionSimulation(
            request_id=request_id,
            status="submit_failed",
            requested_ts=requested_ts,
            ack_ts=ack_ts,
            landing_ts=landing_ts,
            expires_ts=expires_ts,
            terminal=True,
            reason="landing_timeout",
        )
    if now_ms >= landing_ts:
        return SubmissionSimulation(
            request_id=request_id,
            status="landed",
            requested_ts=requested_ts,
            ack_ts=ack_ts,
            landing_ts=landing_ts,
            expires_ts=expires_ts,
            terminal=False,
            reason="landed",
        )
    if now_ms >= ack_ts:
        return SubmissionSimulation(
            request_id=request_id,
            status="submit_acked",
            requested_ts=requested_ts,
            ack_ts=ack_ts,
            landing_ts=landing_ts,
            expires_ts=expires_ts,
            terminal=False,
            reason="submit_acked",
        )
    return SubmissionSimulation(
        request_id=request_id,
        status="submit_requested",
        requested_ts=requested_ts,
        ack_ts=ack_ts,
        landing_ts=landing_ts,
        expires_ts=expires_ts,
        terminal=False,
        reason="submit_requested",
    )

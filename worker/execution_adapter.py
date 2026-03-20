from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Any

from worker.submission_simulator import advance_submission_plan, create_submission_plan


logger = logging.getLogger(__name__)


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except Exception:
        return None


def _intent_dict(transaction_intent: Any) -> dict[str, Any]:
    if hasattr(transaction_intent, "as_dict"):
        payload = transaction_intent.as_dict()
        if isinstance(payload, dict):
            return payload
    if isinstance(transaction_intent, dict):
        return transaction_intent
    raise ValueError("transaction_intent_invalid")


@dataclass(frozen=True)
class ExecutionAdapterConstraints:
    require_fresh_quotes: bool
    require_sell_route: bool
    no_signing: bool
    no_broadcast: bool
    execution_mode_target: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SubmissionAttempt:
    adapter_kind: str
    adapter_version: str
    request_id: str
    intent_id: str
    token: str
    side: str
    status: str
    requested_ts: int
    ack_ts: int
    landing_ts: int
    expires_ts: int
    quote_expires_ts: int | None
    terminal: bool
    reason: str | None
    constraints: ExecutionAdapterConstraints

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter_kind": self.adapter_kind,
            "adapter_version": self.adapter_version,
            "request_id": self.request_id,
            "intent_id": self.intent_id,
            "token": self.token,
            "side": self.side,
            "status": self.status,
            "requested_ts": self.requested_ts,
            "ack_ts": self.ack_ts,
            "landing_ts": self.landing_ts,
            "expires_ts": self.expires_ts,
            "quote_expires_ts": self.quote_expires_ts,
            "terminal": self.terminal,
            "reason": self.reason,
            "constraints": self.constraints.as_dict(),
        }


def _constraints_from_intent(intent: dict[str, Any]) -> ExecutionAdapterConstraints:
    constraints = intent.get("constraints") if isinstance(intent.get("constraints"), dict) else {}
    return ExecutionAdapterConstraints(
        require_fresh_quotes=bool(constraints.get("require_fresh_quotes", True)),
        require_sell_route=bool(constraints.get("require_sell_route", True)),
        no_signing=bool(constraints.get("no_signing", True)),
        no_broadcast=bool(constraints.get("no_broadcast", True)),
        execution_mode_target=str(constraints.get("execution_mode_target") or "").strip() or None,
    )


def create_submission_attempt(*, transaction_intent: Any, now_ts: int | None = None) -> SubmissionAttempt:
    intent = _intent_dict(transaction_intent)
    quote = intent.get("quote") if isinstance(intent.get("quote"), dict) else {}
    plan = create_submission_plan(now_ts=now_ts)
    attempt = SubmissionAttempt(
        adapter_kind="shadow_submission_simulator",
        adapter_version="v1",
        request_id=plan.request_id,
        intent_id=str(intent.get("intent_id") or ""),
        token=str(intent.get("token") or ""),
        side=str(intent.get("side") or ""),
        status=plan.status,
        requested_ts=plan.requested_ts,
        ack_ts=plan.ack_ts,
        landing_ts=plan.landing_ts,
        expires_ts=plan.expires_ts,
        quote_expires_ts=_to_int(quote.get("quote_expires_ts")),
        terminal=plan.terminal,
        reason=plan.reason,
        constraints=_constraints_from_intent(intent),
    )
    logger.info(
        "[execution-adapter-create] adapter=%s intent_id=%s token=%s request_id=%s status=%s quote_expires_ts=%s",
        attempt.adapter_kind,
        attempt.intent_id,
        attempt.token,
        attempt.request_id,
        attempt.status,
        attempt.quote_expires_ts or 0,
    )
    return attempt


def advance_submission_attempt(*, transaction_intent: Any, attempt: dict[str, Any] | None, now_ts: int | None = None) -> SubmissionAttempt:
    intent = _intent_dict(transaction_intent)
    prior = attempt if isinstance(attempt, dict) else {}
    quote = intent.get("quote") if isinstance(intent.get("quote"), dict) else {}
    plan = advance_submission_plan(
        {
            "request_id": prior.get("request_id"),
            "status": prior.get("status"),
            "requested_ts": prior.get("requested_ts"),
            "ack_ts": prior.get("ack_ts"),
            "landing_ts": prior.get("landing_ts"),
            "expires_ts": prior.get("expires_ts"),
        },
        now_ts=now_ts,
        quote_expires_ts=_to_int(prior.get("quote_expires_ts")) or _to_int(quote.get("quote_expires_ts")),
    )
    advanced = SubmissionAttempt(
        adapter_kind=str(prior.get("adapter_kind") or "shadow_submission_simulator"),
        adapter_version=str(prior.get("adapter_version") or "v1"),
        request_id=plan.request_id,
        intent_id=str(intent.get("intent_id") or ""),
        token=str(intent.get("token") or ""),
        side=str(intent.get("side") or ""),
        status=plan.status,
        requested_ts=plan.requested_ts,
        ack_ts=plan.ack_ts,
        landing_ts=plan.landing_ts,
        expires_ts=plan.expires_ts,
        quote_expires_ts=_to_int(prior.get("quote_expires_ts")) or _to_int(quote.get("quote_expires_ts")),
        terminal=plan.terminal,
        reason=plan.reason,
        constraints=_constraints_from_intent(intent),
    )
    logger.info(
        "[execution-adapter-advance] adapter=%s intent_id=%s token=%s request_id=%s status=%s terminal=%s reason=%s",
        advanced.adapter_kind,
        advanced.intent_id,
        advanced.token,
        advanced.request_id,
        advanced.status,
        1 if advanced.terminal else 0,
        advanced.reason or "",
    )
    return advanced

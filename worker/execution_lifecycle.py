from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


STATE_ENTRY_RECORDED = "entry_recorded"
STATE_MONITORING = "monitoring"
STATE_EXIT_TRIGGERED = "exit_triggered"
STATE_CLOSED = "closed"
STATE_QUOTE_EXPIRED = "quote_expired"
STATE_MONITOR_ERROR = "monitor_error"

TERMINAL_STATES = {STATE_CLOSED, STATE_QUOTE_EXPIRED}

TransitionReason = Literal[
    "entry_recorded",
    "mark_to_market",
    "take_profit",
    "stop_loss",
    "time_stop",
    "quote_expired",
    "monitor_error",
]


@dataclass(frozen=True)
class ExecutionTransition:
    from_state: str
    to_state: str
    reason: str
    terminal: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionTransitionPlan:
    current_state: str
    next_state: str
    terminal: bool
    transitions: list[ExecutionTransition]

    def as_dict(self) -> dict:
        return {
            "current_state": self.current_state,
            "next_state": self.next_state,
            "terminal": self.terminal,
            "transitions": [item.as_dict() for item in self.transitions],
        }


def initial_execution_state() -> str:
    return STATE_ENTRY_RECORDED


def plan_shadow_monitor_transition(
    current_state: str | None,
    *,
    exit_reason: str | None,
    quote_expired: bool = False,
    monitor_error: bool = False,
) -> ExecutionTransitionPlan:
    state = str(current_state or STATE_ENTRY_RECORDED)
    transitions: list[ExecutionTransition] = []

    if quote_expired:
        transitions.append(
            ExecutionTransition(
                from_state=state,
                to_state=STATE_QUOTE_EXPIRED,
                reason="quote_expired",
                terminal=True,
            )
        )
        return ExecutionTransitionPlan(
            current_state=state,
            next_state=STATE_QUOTE_EXPIRED,
            terminal=True,
            transitions=transitions,
        )

    if monitor_error:
        if state != STATE_MONITOR_ERROR:
            transitions.append(
                ExecutionTransition(
                    from_state=state,
                    to_state=STATE_MONITOR_ERROR,
                    reason="monitor_error",
                    terminal=False,
                )
            )
            state = STATE_MONITOR_ERROR
        return ExecutionTransitionPlan(
            current_state=str(current_state or STATE_ENTRY_RECORDED),
            next_state=state,
            terminal=False,
            transitions=transitions,
        )

    if state not in {STATE_MONITORING, STATE_EXIT_TRIGGERED, STATE_CLOSED}:
        transitions.append(
            ExecutionTransition(
                from_state=state,
                to_state=STATE_MONITORING,
                reason="mark_to_market",
                terminal=False,
            )
        )
        state = STATE_MONITORING

    if exit_reason:
        transitions.append(
            ExecutionTransition(
                from_state=state,
                to_state=STATE_EXIT_TRIGGERED,
                reason=exit_reason,
                terminal=False,
            )
        )
        transitions.append(
            ExecutionTransition(
                from_state=STATE_EXIT_TRIGGERED,
                to_state=STATE_CLOSED,
                reason=exit_reason,
                terminal=True,
            )
        )
        state = STATE_CLOSED

    return ExecutionTransitionPlan(
        current_state=str(current_state or STATE_ENTRY_RECORDED),
        next_state=state,
        terminal=state in TERMINAL_STATES,
        transitions=transitions,
    )

from worker.execution_lifecycle import (
    STATE_CLOSED,
    STATE_ENTRY_RECORDED,
    STATE_LANDED,
    STATE_MONITOR_ERROR,
    STATE_MONITORING,
    STATE_QUOTE_EXPIRED,
    STATE_SUBMIT_ACKED,
    STATE_SUBMIT_INTENT_RECORDED,
    STATE_SUBMIT_REQUESTED,
    initial_execution_state,
    plan_shadow_entry_transition,
    plan_shadow_monitor_transition,
    plan_shadow_submission_transition,
)


def test_initial_execution_state_is_entry_recorded():
    assert initial_execution_state() == STATE_ENTRY_RECORDED


def test_monitor_transition_moves_entry_to_monitoring():
    plan = plan_shadow_monitor_transition(STATE_SUBMIT_INTENT_RECORDED, exit_reason=None)

    assert plan.next_state == STATE_MONITORING
    assert plan.terminal is False
    assert [item.to_state for item in plan.transitions] == [STATE_MONITORING]


def test_entry_transition_records_quote_validation_and_submit_intent():
    plan = plan_shadow_entry_transition(STATE_ENTRY_RECORDED)

    assert plan.next_state == STATE_SUBMIT_INTENT_RECORDED
    assert plan.terminal is False
    assert [item.to_state for item in plan.transitions] == ["quote_validated", STATE_SUBMIT_INTENT_RECORDED]


def test_monitor_transition_closes_on_take_profit():
    plan = plan_shadow_monitor_transition(STATE_SUBMIT_INTENT_RECORDED, exit_reason="take_profit")

    assert plan.next_state == STATE_CLOSED
    assert plan.terminal is True
    assert [item.to_state for item in plan.transitions] == [STATE_MONITORING, "exit_triggered", STATE_CLOSED]


def test_monitor_transition_marks_non_terminal_error():
    plan = plan_shadow_monitor_transition(STATE_ENTRY_RECORDED, exit_reason=None, monitor_error=True)

    assert plan.next_state == STATE_MONITOR_ERROR
    assert plan.terminal is False
    assert [item.to_state for item in plan.transitions] == [STATE_MONITOR_ERROR]


def test_monitor_transition_can_expire_quote():
    plan = plan_shadow_monitor_transition(STATE_SUBMIT_INTENT_RECORDED, exit_reason=None, quote_expired=True)

    assert plan.next_state == STATE_QUOTE_EXPIRED
    assert plan.terminal is True
    assert [item.to_state for item in plan.transitions] == [STATE_QUOTE_EXPIRED]


def test_submission_transition_progresses_request_to_landed():
    requested = plan_shadow_submission_transition(STATE_SUBMIT_INTENT_RECORDED, submission_status="submit_requested")
    acked = plan_shadow_submission_transition(STATE_SUBMIT_REQUESTED, submission_status="submit_acked")
    landed = plan_shadow_submission_transition(STATE_SUBMIT_ACKED, submission_status="landed")

    assert requested.next_state == STATE_SUBMIT_REQUESTED
    assert acked.next_state == STATE_SUBMIT_ACKED
    assert landed.next_state == STATE_LANDED

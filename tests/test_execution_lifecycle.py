from worker.execution_lifecycle import (
    STATE_CLOSED,
    STATE_ENTRY_RECORDED,
    STATE_MONITOR_ERROR,
    STATE_MONITORING,
    initial_execution_state,
    plan_shadow_monitor_transition,
)


def test_initial_execution_state_is_entry_recorded():
    assert initial_execution_state() == STATE_ENTRY_RECORDED


def test_monitor_transition_moves_entry_to_monitoring():
    plan = plan_shadow_monitor_transition(STATE_ENTRY_RECORDED, exit_reason=None)

    assert plan.next_state == STATE_MONITORING
    assert plan.terminal is False
    assert [item.to_state for item in plan.transitions] == [STATE_MONITORING]


def test_monitor_transition_closes_on_take_profit():
    plan = plan_shadow_monitor_transition(STATE_ENTRY_RECORDED, exit_reason="take_profit")

    assert plan.next_state == STATE_CLOSED
    assert plan.terminal is True
    assert [item.to_state for item in plan.transitions] == [STATE_MONITORING, "exit_triggered", STATE_CLOSED]


def test_monitor_transition_marks_non_terminal_error():
    plan = plan_shadow_monitor_transition(STATE_ENTRY_RECORDED, exit_reason=None, monitor_error=True)

    assert plan.next_state == STATE_MONITOR_ERROR
    assert plan.terminal is False
    assert [item.to_state for item in plan.transitions] == [STATE_MONITOR_ERROR]

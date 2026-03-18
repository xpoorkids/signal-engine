from worker.submission_simulator import advance_submission_plan, create_submission_plan


def test_create_submission_plan_sets_request_ack_and_landing_windows():
    plan = create_submission_plan(now_ts=1000)

    assert plan.request_id
    assert plan.status == "submit_requested"
    assert plan.requested_ts == 1_000_000
    assert plan.ack_ts >= plan.requested_ts
    assert plan.landing_ts >= plan.ack_ts
    assert plan.expires_ts >= plan.landing_ts


def test_advance_submission_plan_progresses_to_acked_and_landed():
    plan = create_submission_plan(now_ts=1000)

    acked = advance_submission_plan(plan.as_dict(), now_ts=1001)
    landed = advance_submission_plan(plan.as_dict(), now_ts=1003)

    assert acked.status in {"submit_requested", "submit_acked"}
    assert landed.status in {"submit_acked", "landed"}


def test_advance_submission_plan_expires_on_quote_deadline():
    plan = create_submission_plan(now_ts=1000)
    expired = advance_submission_plan(plan.as_dict(), now_ts=1002, quote_expires_ts=1001)

    assert expired.status == "submit_expired"
    assert expired.terminal is True

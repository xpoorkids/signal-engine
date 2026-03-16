from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.services.signal_learning_service import (
    activate_policy_rollout,
    auto_create_policy_approvals,
    auto_promote_policy_canaries,
    auto_schedule_policy_canaries,
    create_policy_approval,
    create_policy_profile,
    evaluate_shadow_policy,
    evaluate_policy_guardrails,
    get_engine_health_digest,
    get_policy_automation_status,
    get_diagnostics_summary,
    get_learning_digest,
    get_latest_learning_report,
    get_latest_policy_replay,
    get_learning_report,
    get_policy_approval,
    list_policy_profiles,
    list_policy_approvals,
    list_policy_rollouts,
    list_policy_rollout_events,
    get_policy_trace_summary,
    get_policy_replay,
    resolve_live_policy,
    run_policy_automation_cycle,
    run_policy_replay,
    render_engine_health_html,
    render_diagnostics_html,
    render_learning_digest_html,
    render_learning_report_html,
    update_policy_approval_status,
)
from app.services.tuning_service import (
    build_tuning_profiles,
    build_tuning_proposals,
    create_tuning_approval,
    apply_rollout_verification,
    apply_pending_rollout_verifications,
    dispatch_ops_digest,
    get_config_drift_report,
    get_latest_tuning_approval,
    get_ops_digest,
    get_operator_command_center,
    get_rollout_verification,
    get_tuning_rollout_summary,
    list_notification_incidents,
    list_rollout_notifications,
    list_tuning_approvals,
    render_notification_incidents_html,
    render_ops_digest_html,
    render_ops_digest_text,
    render_operator_command_center_html,
    render_profile_apply_diff,
    render_profile_env_snippet,
    render_rollout_notifications_html,
    render_rollout_verification_html,
    render_tuning_rollout_summary_html,
    render_tuning_approvals_html,
    render_tuning_apply_diff,
    render_tuning_env_snippet,
    render_latest_tuning_bundle_artifact,
    render_tuning_profiles_html,
    render_tuning_proposals_html,
    update_incident_state,
    update_rollout_notification_state,
    update_tuning_approval_status,
)


router = APIRouter()


@router.get("/learning/report/latest")
def learning_report_latest():
    report = get_latest_learning_report()
    if report is None:
        raise HTTPException(status_code=404, detail="learning_report_not_found")
    return report


@router.get("/learning/report/latest/dashboard")
def learning_report_latest_dashboard():
    try:
        return HTMLResponse(content=render_learning_report_html())
    except KeyError:
        raise HTTPException(status_code=404, detail="learning_report_not_found")


@router.get("/learning/report/latest/digest")
def learning_report_latest_digest():
    digest = get_learning_digest()
    if digest is None:
        raise HTTPException(status_code=404, detail="learning_report_not_found")
    return digest


@router.get("/learning/report/latest/digest/dashboard")
def learning_report_latest_digest_dashboard():
    try:
        return HTMLResponse(content=render_learning_digest_html())
    except KeyError:
        raise HTTPException(status_code=404, detail="learning_report_not_found")


@router.get("/learning/report/{report_date}")
def learning_report_by_date(report_date: str):
    report = get_learning_report(report_date)
    if report is None:
        raise HTTPException(status_code=404, detail="learning_report_not_found")
    return report


@router.get("/learning/report/{report_date}/dashboard")
def learning_report_dashboard(report_date: str):
    try:
        return HTMLResponse(content=render_learning_report_html(report_date))
    except KeyError:
        raise HTTPException(status_code=404, detail="learning_report_not_found")


@router.get("/learning/report/{report_date}/digest")
def learning_report_digest(report_date: str):
    digest = get_learning_digest(report_date)
    if digest is None:
        raise HTTPException(status_code=404, detail="learning_report_not_found")
    return digest


@router.get("/learning/report/{report_date}/digest/dashboard")
def learning_report_digest_dashboard(report_date: str):
    try:
        return HTMLResponse(content=render_learning_digest_html(report_date))
    except KeyError:
        raise HTTPException(status_code=404, detail="learning_report_not_found")


@router.get("/learning/diagnostics/summary")
def learning_diagnostics_summary(hours: int = 24):
    return get_diagnostics_summary(hours=max(1, hours))


@router.get("/learning/policy/traces")
def learning_policy_traces(hours: int = 24, limit: int = 50, stage: str | None = None, decision: str | None = None):
    return get_policy_trace_summary(hours=max(1, hours), limit=max(1, limit), stage=stage, decision=decision)


@router.get("/learning/policy/profiles")
def learning_policy_profiles(limit: int = 20, policy_name: str | None = None):
    return {"profiles": list_policy_profiles(limit=max(1, limit), policy_name=policy_name)}


@router.post("/learning/policy/profiles")
def learning_policy_profiles_create(payload: dict[str, object] = Body(...)):
    try:
        return create_policy_profile(
            policy_name=str(payload.get("policy_name") or ""),
            policy_version=str(payload.get("policy_version") or ""),
            config=payload.get("config") if isinstance(payload.get("config"), dict) else None,
            description=str(payload.get("description") or "") or None,
            created_by=str(payload.get("created_by") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/learning/policy/rollouts")
def learning_policy_rollouts(limit: int = 20, active_only: bool = False):
    return {"rollouts": list_policy_rollouts(limit=max(1, limit), active_only=active_only)}


@router.post("/learning/policy/rollouts")
def learning_policy_rollouts_create(payload: dict[str, object] = Body(...)):
    try:
        return activate_policy_rollout(
            policy_name=str(payload.get("policy_name") or ""),
            policy_version=str(payload.get("policy_version") or ""),
            rollout_mode=str(payload.get("rollout_mode") or "active"),
            rollout_status=str(payload.get("rollout_status") or "active"),
            stage_scope=str(payload.get("stage_scope") or "") or None,
            traffic_percent=int(payload.get("traffic_percent") or 100),
            priority=int(payload.get("priority") or 100),
            activated_by=str(payload.get("activated_by") or "") or None,
            notes=str(payload.get("notes") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/learning/policy/resolve")
def learning_policy_resolve(stage: str, token: str | None = None):
    return resolve_live_policy(stage=stage, token=token)


@router.get("/learning/policy/approvals")
def learning_policy_approvals(limit: int = 20, approval_status: str | None = None):
    return {"approvals": list_policy_approvals(limit=max(1, limit), approval_status=approval_status)}


@router.post("/learning/policy/approvals")
def learning_policy_approvals_create(payload: dict[str, object] = Body(...)):
    try:
        return create_policy_approval(
            policy_name=str(payload.get("policy_name") or ""),
            policy_version=str(payload.get("policy_version") or ""),
            source_type=str(payload.get("source_type") or ""),
            source_ref=str(payload.get("source_ref") or "") or None,
            notes=str(payload.get("notes") or "") or None,
            approved_by=str(payload.get("approved_by") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/learning/policy/approvals/{approval_id}")
def learning_policy_approvals_by_id(approval_id: str):
    approval = get_policy_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="policy_approval_not_found")
    return approval


@router.post("/learning/policy/approvals/{approval_id}/status")
def learning_policy_approvals_status(approval_id: str, payload: dict[str, object] = Body(...)):
    try:
        return update_policy_approval_status(
            approval_id,
            approval_status=str(payload.get("approval_status") or ""),
            approved_by=str(payload.get("approved_by") or "") or None,
            notes=str(payload.get("notes") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="policy_approval_not_found")


@router.get("/learning/policy/events")
def learning_policy_events(limit: int = 50, event_type: str | None = None):
    return {"events": list_policy_rollout_events(limit=max(1, limit), event_type=event_type)}


@router.post("/learning/policy/guardrails/evaluate")
def learning_policy_guardrails_evaluate(payload: dict[str, object] = Body(default={})):
    return evaluate_policy_guardrails(
        hours=max(1, int(payload.get("hours") or 24)),
        min_samples=max(1, int(payload.get("min_samples") or 3)),
        max_negative_rate=float(payload.get("max_negative_rate") or 60.0),
        auto_apply=bool(payload.get("auto_apply") or False),
    )


@router.get("/learning/policy/automation/status")
def learning_policy_automation_status():
    return get_policy_automation_status()


@router.post("/learning/policy/automation/approvals")
def learning_policy_automation_approvals(payload: dict[str, object] = Body(default={})):
    return auto_create_policy_approvals(limit=int(payload.get("limit") or 20))


@router.post("/learning/policy/automation/canaries")
def learning_policy_automation_canaries(payload: dict[str, object] = Body(default={})):
    return auto_schedule_policy_canaries(hours=max(1, int(payload.get("hours") or 24)))


@router.post("/learning/policy/automation/promote")
def learning_policy_automation_promote(payload: dict[str, object] = Body(default={})):
    return auto_promote_policy_canaries(hours=max(1, int(payload.get("hours") or 24)))


@router.post("/learning/policy/automation/run")
def learning_policy_automation_run(payload: dict[str, object] = Body(default={})):
    return run_policy_automation_cycle(
        hours=max(1, int(payload.get("hours") or 24)),
        replay_limit=int(payload.get("replay_limit") or 20),
    )


@router.get("/learning/policy/shadow")
def learning_policy_shadow(
    hours: int = 24,
    limit: int = 200,
    stage: str | None = None,
    policy_name: str | None = None,
    policy_version: str | None = None,
    candidate_attention_min: float | None = None,
    candidate_creator_min: float | None = None,
    promoted_confidence_min: float | None = None,
    promoted_attention_min: float | None = None,
    promoted_risk_max: float | None = None,
    promoted_liquidity_min: float | None = None,
    promoted_buyers_15m_min: int | None = None,
):
    overrides = {
        "candidate_attention_min": candidate_attention_min,
        "candidate_creator_min": candidate_creator_min,
        "promoted_confidence_min": promoted_confidence_min,
        "promoted_attention_min": promoted_attention_min,
        "promoted_risk_max": promoted_risk_max,
        "promoted_liquidity_min": promoted_liquidity_min,
        "promoted_buyers_15m_min": promoted_buyers_15m_min,
    }
    return evaluate_shadow_policy(
        hours=max(1, hours),
        limit=max(1, limit),
        stage=stage,
        policy_name=policy_name,
        policy_version=policy_version,
        overrides=overrides,
    )


@router.post("/learning/policy/replay/run")
def learning_policy_replay_run(payload: dict[str, object] = Body(default={})):
    overrides = {
        "candidate_attention_min": payload.get("candidate_attention_min"),
        "candidate_creator_min": payload.get("candidate_creator_min"),
        "promoted_confidence_min": payload.get("promoted_confidence_min"),
        "promoted_attention_min": payload.get("promoted_attention_min"),
        "promoted_risk_max": payload.get("promoted_risk_max"),
        "promoted_liquidity_min": payload.get("promoted_liquidity_min"),
        "promoted_buyers_15m_min": payload.get("promoted_buyers_15m_min"),
    }
    return run_policy_replay(
        hours=max(1, int(payload.get("hours") or 24)),
        limit=max(1, int(payload.get("limit") or 500)),
        stage=str(payload.get("stage") or "") or None,
        policy_name=str(payload.get("policy_name") or "") or None,
        policy_version=str(payload.get("policy_version") or "") or None,
        overrides=overrides,
    )


@router.get("/learning/policy/replay/latest")
def learning_policy_replay_latest():
    replay = get_latest_policy_replay()
    if replay is None:
        raise HTTPException(status_code=404, detail="policy_replay_not_found")
    return replay


@router.get("/learning/policy/replay/{run_id}")
def learning_policy_replay_by_id(run_id: str):
    replay = get_policy_replay(run_id)
    if replay is None:
        raise HTTPException(status_code=404, detail="policy_replay_not_found")
    return replay


@router.get("/learning/health")
def learning_engine_health(hours: int = 6):
    return get_engine_health_digest(hours=max(1, hours))


@router.get("/learning/health/dashboard")
def learning_engine_health_dashboard(hours: int = 6):
    return HTMLResponse(content=render_engine_health_html(hours=max(1, hours)))


@router.get("/learning/tuning/proposals")
def learning_tuning_proposals(hours: int = 72):
    return build_tuning_proposals(hours=max(1, hours))


@router.get("/learning/tuning/proposals/dashboard")
def learning_tuning_proposals_dashboard(hours: int = 72):
    return HTMLResponse(content=render_tuning_proposals_html(hours=max(1, hours)))


@router.get("/learning/tuning/proposals/env")
def learning_tuning_proposals_env(hours: int = 72):
    return PlainTextResponse(content=render_tuning_env_snippet(hours=max(1, hours)))


@router.get("/learning/tuning/proposals/diff")
def learning_tuning_proposals_diff(hours: int = 72):
    return PlainTextResponse(content=render_tuning_apply_diff(hours=max(1, hours)))


@router.get("/learning/tuning/profiles")
def learning_tuning_profiles(hours: int = 72):
    return build_tuning_profiles(hours=max(1, hours))


@router.get("/learning/tuning/profiles/dashboard")
def learning_tuning_profiles_dashboard(hours: int = 72):
    return HTMLResponse(content=render_tuning_profiles_html(hours=max(1, hours)))


@router.get("/learning/tuning/profiles/{profile_name}/env")
def learning_tuning_profile_env(profile_name: str, hours: int = 72):
    return PlainTextResponse(content=render_profile_env_snippet(profile_name, hours=max(1, hours)))


@router.get("/learning/tuning/profiles/{profile_name}/diff")
def learning_tuning_profile_diff(profile_name: str, hours: int = 72):
    return PlainTextResponse(content=render_profile_apply_diff(profile_name, hours=max(1, hours)))


@router.get("/learning/tuning/approvals")
def learning_tuning_approvals(
    limit: int = 20,
    approval_kind: str | None = None,
    artifact_kind: str | None = None,
    target_name: str | None = None,
    rollout_status: str | None = None,
    q: str | None = None,
):
    return {
        "approvals": list_tuning_approvals(
            limit=max(1, limit),
            approval_kind=approval_kind,
            artifact_kind=artifact_kind,
            target_name=target_name,
            rollout_status=rollout_status,
            query=q,
        )
    }


@router.post("/learning/tuning/approvals")
def learning_tuning_approvals_create(payload: dict[str, object] = Body(...)):
    try:
        approval = create_tuning_approval(
            approval_kind=str(payload.get("approval_kind") or ""),
            artifact_kind=str(payload.get("artifact_kind") or ""),
            hours=int(payload.get("hours") or 72),
            target_name=str(payload.get("target_name") or "") or None,
            approved_by=str(payload.get("approved_by") or "") or None,
            notes=str(payload.get("notes") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return approval


@router.post("/learning/tuning/approvals/{approval_id}/status")
def learning_tuning_approvals_status(approval_id: str, payload: dict[str, object] = Body(...)):
    try:
        approval = update_tuning_approval_status(
            approval_id,
            rollout_status=str(payload.get("rollout_status") or ""),
            notes=str(payload.get("notes") or "") or None,
            deployment_service=str(payload.get("deployment_service") or "") or None,
            deployment_sha=str(payload.get("deployment_sha") or "") or None,
            deployment_env=str(payload.get("deployment_env") or "") or None,
            allow_misaligned=bool(payload.get("allow_misaligned") or False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="tuning_approval_not_found")
    return approval


@router.get("/learning/tuning/approvals/dashboard")
def learning_tuning_approvals_dashboard(
    limit: int = 20,
    approval_kind: str | None = None,
    artifact_kind: str | None = None,
    target_name: str | None = None,
    rollout_status: str | None = None,
    q: str | None = None,
):
    return HTMLResponse(
        content=render_tuning_approvals_html(
            limit=max(1, limit),
            approval_kind=approval_kind,
            artifact_kind=artifact_kind,
            target_name=target_name,
            rollout_status=rollout_status,
            query=q,
        )
    )


@router.get("/learning/tuning/approvals/latest")
def learning_tuning_approvals_latest(
    approval_kind: str,
    artifact_kind: str,
    target_name: str | None = None,
    rollout_status: str = "approved",
):
    try:
        approval = get_latest_tuning_approval(
            approval_kind=approval_kind,
            artifact_kind=artifact_kind,
            target_name=target_name,
            rollout_status=rollout_status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if approval is None:
        raise HTTPException(status_code=404, detail="tuning_approval_not_found")
    return approval


@router.get("/learning/tuning/approvals/latest/artifact")
def learning_tuning_approvals_latest_artifact(
    approval_kind: str,
    artifact_kind: str,
    target_name: str | None = None,
    rollout_status: str = "approved",
):
    try:
        approval = get_latest_tuning_approval(
            approval_kind=approval_kind,
            artifact_kind=artifact_kind,
            target_name=target_name,
            rollout_status=rollout_status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if approval is None:
        raise HTTPException(status_code=404, detail="tuning_approval_not_found")
    return PlainTextResponse(content=str(approval.get("artifact_text") or ""))


@router.get("/learning/tuning/approvals/latest/bundle")
def learning_tuning_approvals_latest_bundle(
    artifact_kind: str = "env",
    rollout_status: str = "rolled_out",
):
    try:
        return PlainTextResponse(
            content=render_latest_tuning_bundle_artifact(
                artifact_kind=artifact_kind,
                rollout_status=rollout_status,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/learning/tuning/drift")
def learning_tuning_drift(target_name: str, rollout_status: str = "rolled_out"):
    try:
        return get_config_drift_report(target_name=target_name, rollout_status=rollout_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/learning/tuning/rollout/summary")
def learning_tuning_rollout_summary():
    return get_tuning_rollout_summary()


@router.get("/learning/tuning/rollout/dashboard")
def learning_tuning_rollout_dashboard():
    return HTMLResponse(content=render_tuning_rollout_summary_html())


@router.get("/learning/tuning/verification")
def learning_tuning_verification(
    approval_id: str | None = None,
    target_name: str | None = None,
    deployment_service: str | None = None,
    baseline_hours: int = 24,
    post_hours: int = 24,
):
    try:
        return get_rollout_verification(
            approval_id=approval_id,
            target_name=target_name,
            deployment_service=deployment_service,
            baseline_hours=max(1, baseline_hours),
            post_hours=max(1, post_hours),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="rollout_not_found")


@router.get("/learning/tuning/verification/dashboard")
def learning_tuning_verification_dashboard(
    approval_id: str | None = None,
    target_name: str | None = None,
    deployment_service: str | None = None,
    baseline_hours: int = 24,
    post_hours: int = 24,
):
    try:
        return HTMLResponse(
            content=render_rollout_verification_html(
                approval_id=approval_id,
                target_name=target_name,
                deployment_service=deployment_service,
                baseline_hours=max(1, baseline_hours),
                post_hours=max(1, post_hours),
            )
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="rollout_not_found")


@router.post("/learning/tuning/verification/apply")
def learning_tuning_verification_apply(payload: dict[str, object] = Body(...)):
    try:
        return apply_rollout_verification(
            approval_id=str(payload.get("approval_id") or "") or None,
            target_name=str(payload.get("target_name") or "") or None,
            deployment_service=str(payload.get("deployment_service") or "") or None,
            baseline_hours=max(1, int(payload.get("baseline_hours") or 24)),
            post_hours=max(1, int(payload.get("post_hours") or 24)),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="rollout_not_found")


@router.post("/learning/tuning/verification/run")
def learning_tuning_verification_run(payload: dict[str, object] = Body(default={})):
    return apply_pending_rollout_verifications(
        baseline_hours=max(1, int(payload.get("baseline_hours") or 24)),
        post_hours=max(1, int(payload.get("post_hours") or 24)),
        limit=max(1, int(payload.get("limit") or 20)),
        force=bool(payload.get("force") or False),
    )


@router.get("/learning/tuning/notifications")
def learning_tuning_notifications(limit: int = 20, active_only: bool = False):
    return {"notifications": list_rollout_notifications(limit=max(1, limit), active_only=active_only)}


@router.get("/learning/tuning/incidents")
def learning_tuning_incidents(limit: int = 20, active_only: bool = False):
    return {"incidents": list_notification_incidents(limit=max(1, limit), active_only=active_only)}


@router.get("/learning/tuning/notifications/dashboard")
def learning_tuning_notifications_dashboard(limit: int = 20, active_only: bool = False):
    return HTMLResponse(content=render_rollout_notifications_html(limit=max(1, limit), active_only=active_only))


@router.get("/learning/tuning/incidents/dashboard")
def learning_tuning_incidents_dashboard(limit: int = 20, active_only: bool = False):
    return HTMLResponse(content=render_notification_incidents_html(limit=max(1, limit), active_only=active_only))


@router.post("/learning/tuning/incidents/state")
def learning_tuning_incident_state(payload: dict[str, object] = Body(...)):
    try:
        incident = update_incident_state(
            event_type=str(payload.get("event_type") or ""),
            target_name=str(payload.get("target_name") or "") or None,
            deployment_service=str(payload.get("deployment_service") or "") or None,
            acknowledged=payload.get("acknowledged") if "acknowledged" in payload else None,
            acknowledged_by=str(payload.get("acknowledged_by") or "") or None,
            snooze_minutes=int(payload.get("snooze_minutes")) if payload.get("snooze_minutes") is not None else None,
            unsnooze=bool(payload.get("unsnooze") or False),
            resolved=payload.get("resolved") if "resolved" in payload else None,
            resolved_by=str(payload.get("resolved_by") or "") or None,
            resolution_note=str(payload.get("resolution_note") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="incident_not_found")
    return incident


@router.post("/learning/tuning/notifications/{notification_id}/state")
def learning_tuning_notification_state(notification_id: str, payload: dict[str, object] = Body(...)):
    try:
        notification = update_rollout_notification_state(
            notification_id,
            acknowledged=payload.get("acknowledged") if "acknowledged" in payload else None,
            acknowledged_by=str(payload.get("acknowledged_by") or "") or None,
            snooze_minutes=int(payload.get("snooze_minutes")) if payload.get("snooze_minutes") is not None else None,
            unsnooze=bool(payload.get("unsnooze") or False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="rollout_notification_not_found")
    return notification


@router.get("/learning/command-center")
def learning_command_center(hours: int = 24):
    return get_operator_command_center(hours=max(1, hours))


@router.get("/learning/command-center/dashboard")
def learning_command_center_dashboard(hours: int = 24):
    return HTMLResponse(content=render_operator_command_center_html(hours=max(1, hours)))


@router.get("/learning/ops/digest")
def learning_ops_digest(hours: int = 24):
    return get_ops_digest(hours=max(1, hours))


@router.get("/learning/ops/digest/dashboard")
def learning_ops_digest_dashboard(hours: int = 24):
    return HTMLResponse(content=render_ops_digest_html(hours=max(1, hours)))


@router.get("/learning/ops/digest/text")
def learning_ops_digest_text(hours: int = 24):
    return PlainTextResponse(content=render_ops_digest_text(hours=max(1, hours)))


@router.post("/learning/ops/digest/send")
def learning_ops_digest_send(payload: dict[str, object] = Body(default={})):
    hours = max(1, int(payload.get("hours") or 24))
    force = bool(payload.get("force") or False)
    return dispatch_ops_digest(hours=hours, force=force)


@router.get("/learning/diagnostics/dashboard")
def learning_diagnostics_dashboard(hours: int = 24):
    return HTMLResponse(content=render_diagnostics_html(hours=max(1, hours)))

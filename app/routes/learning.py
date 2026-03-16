from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.services.signal_learning_service import (
    get_engine_health_digest,
    get_diagnostics_summary,
    get_learning_digest,
    get_latest_learning_report,
    get_learning_report,
    render_engine_health_html,
    render_diagnostics_html,
    render_learning_digest_html,
    render_learning_report_html,
)
from app.services.tuning_service import (
    build_tuning_profiles,
    build_tuning_proposals,
    create_tuning_approval,
    get_config_drift_report,
    get_latest_tuning_approval,
    list_tuning_approvals,
    render_profile_apply_diff,
    render_profile_env_snippet,
    render_tuning_approvals_html,
    render_tuning_apply_diff,
    render_tuning_env_snippet,
    render_latest_tuning_bundle_artifact,
    render_tuning_profiles_html,
    render_tuning_proposals_html,
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


@router.get("/learning/diagnostics/dashboard")
def learning_diagnostics_dashboard(hours: int = 24):
    return HTMLResponse(content=render_diagnostics_html(hours=max(1, hours)))

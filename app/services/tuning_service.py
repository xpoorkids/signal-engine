from __future__ import annotations

import html
import json
import os
import time
import uuid
import logging
import asyncio
from typing import Any

import requests

from app.services import signal_learning_service as sls
from worker import config as cfg

logger = logging.getLogger(__name__)

PROFILE_CONFIG_KEYS: tuple[str, ...] = (
    "CAND_MIN_TOKEN_AGE_SEC",
    "EARLY_ATTENTION_MIN",
    "EARLY_CREATOR_MIN",
    "PROM_MIN_LIQ_USD",
    "PROMOTION_MIN_ATTENTION",
    "PROMOTION_MAX_RISK",
    "PROMOTE_MIN_CONFIDENCE",
    "ATTENTION_CANDIDATE_THRESHOLD",
    "GATE_PROMOTE_MIN_LIQ",
    "GATE_PROMOTE_MIN_VOL5M",
    "GATE_PROMOTE_MIN_BUYS5M",
)

PROFILE_LABELS: dict[str, str] = {
    "strict": "Tighter promotion and market-quality requirements.",
    "balanced": "Current live baseline with no proposal overrides applied.",
    "aggressive": "Relaxed early filters for faster candidate capture.",
}
CONFIG_KEY_FAMILIES: dict[str, str] = {
    "CAND_MIN_TOKEN_AGE_SEC": "candidate_timing",
    "EARLY_ATTENTION_MIN": "candidate_attention",
    "EARLY_CREATOR_MIN": "candidate_attention",
    "ATTENTION_CANDIDATE_THRESHOLD": "candidate_attention",
    "PROM_MIN_LIQ_USD": "market_quality",
    "GATE_PROMOTE_MIN_LIQ": "market_quality",
    "GATE_PROMOTE_MIN_VOL5M": "market_quality",
    "GATE_PROMOTE_MIN_BUYS5M": "market_quality",
    "PROMOTION_MIN_ATTENTION": "promotion_quality",
    "PROMOTION_MAX_RISK": "promotion_risk",
    "PROMOTE_MIN_CONFIDENCE": "promotion_quality",
}
APPROVAL_STATUSES: set[str] = {"pending", "approved", "rolled_out", "rejected"}
ROLLOUT_COMPARISON_SERVICES: tuple[str, str] = ("worker", "engine")


def _proposal(
    *,
    reason: str,
    action: str,
    config_key: str,
    current_value: Any,
    proposed_value: Any,
    confidence: str,
    rationale: str,
    sample_size: int,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "action": action,
        "config_key": config_key,
        "current_value": current_value,
        "proposed_value": proposed_value,
        "confidence": confidence,
        "sample_size": sample_size,
        "rationale": rationale,
    }


def _format_env_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _format_diff_value(value: Any) -> str:
    return _format_env_value(value)


def _profile_baseline() -> dict[str, Any]:
    return {key: getattr(cfg, key) for key in PROFILE_CONFIG_KEYS}


def _required_aligned_profiles() -> set[str]:
    raw = os.getenv("SIGNAL_ENGINE_REQUIRED_ALIGNED_PROFILES", "").strip()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _family_label(family: str) -> str:
    labels = {
        "candidate_timing": "candidate timing",
        "candidate_attention": "candidate attention",
        "market_quality": "market quality",
        "promotion_quality": "promotion quality",
        "promotion_risk": "promotion risk",
    }
    return labels.get(family, family.replace("_", " "))


def _safe_json_loads(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _default_deployment_metadata() -> dict[str, str]:
    return {
        "deployment_service": (
            os.getenv("SIGNAL_ENGINE_DEPLOY_SERVICE", "").strip()
            or os.getenv("RENDER_SERVICE_NAME", "").strip()
            or os.getenv("RENDER_SERVICE_ID", "").strip()
        ),
        "deployment_sha": (
            os.getenv("SIGNAL_ENGINE_DEPLOY_SHA", "").strip()
            or os.getenv("RENDER_GIT_COMMIT", "").strip()
            or os.getenv("RENDER_GIT_BRANCH", "").strip()
        ),
        "deployment_env": (
            os.getenv("SIGNAL_ENGINE_DEPLOY_ENV", "").strip()
            or os.getenv("RENDER_ENVIRONMENT", "").strip()
            or os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
        ),
    }


def _ops_webhook_url() -> str:
    return (
        os.getenv("SIGNAL_ENGINE_OPS_WEBHOOK_URL", "").strip()
        or os.getenv("OPS_WEBHOOK_URL", "").strip()
    )


def _approval_matches(
    approval: dict[str, Any],
    *,
    approval_kind: str | None = None,
    artifact_kind: str | None = None,
    target_name: str | None = None,
    rollout_status: str | None = None,
    query: str | None = None,
) -> bool:
    if approval_kind and str(approval.get("approval_kind") or "").lower() != approval_kind.lower():
        return False
    if artifact_kind and str(approval.get("artifact_kind") or "").lower() != artifact_kind.lower():
        return False
    if target_name and str(approval.get("target_name") or "").lower() != target_name.lower():
        return False
    if rollout_status and str(approval.get("rollout_status") or "").lower() != rollout_status.lower():
        return False
    if query:
        needle = query.lower()
        haystack = " ".join(
            [
                str(approval.get("approval_id") or ""),
                str(approval.get("approved_by") or ""),
                str(approval.get("approval_kind") or ""),
                str(approval.get("target_name") or ""),
                str(approval.get("artifact_kind") or ""),
                str(approval.get("notes") or ""),
                str(approval.get("artifact_text") or ""),
            ]
        ).lower()
        if needle not in haystack:
            return False
    return True


def _changed_config_entries(approval: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(approval, dict):
        return []
    payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
    kind = str(approval.get("approval_kind") or "").lower()
    target = str(approval.get("target_name") or "").lower()
    entries: list[dict[str, Any]] = []

    if kind == "proposal":
        proposals = payload.get("proposals") if isinstance(payload.get("proposals"), list) else []
        for item in proposals:
            if not isinstance(item, dict):
                continue
            key = str(item.get("config_key") or "").strip()
            if not key:
                continue
            entries.append(
                {
                    "config_key": key,
                    "current_value": item.get("current_value"),
                    "proposed_value": item.get("proposed_value"),
                    "family": CONFIG_KEY_FAMILIES.get(key, "other"),
                }
            )
        return entries

    if kind == "profile":
        diffs = payload.get("profile_diffs") if isinstance(payload.get("profile_diffs"), dict) else {}
        profile_diffs = diffs.get(target) if isinstance(diffs.get(target), list) else []
        for item in profile_diffs:
            if not isinstance(item, dict):
                continue
            key = str(item.get("config_key") or "").strip()
            if not key:
                continue
            entries.append(
                {
                    "config_key": key,
                    "current_value": item.get("current_value"),
                    "proposed_value": item.get("proposed_value"),
                    "family": CONFIG_KEY_FAMILIES.get(key, "other"),
                }
            )
    return entries


def _summarize_changed_config(entries: list[dict[str, Any]]) -> dict[str, Any]:
    families: dict[str, list[str]] = {}
    for item in entries:
        key = str(item.get("config_key") or "").strip()
        if not key:
            continue
        family = str(item.get("family") or "other")
        families.setdefault(family, []).append(key)

    ordered_families = sorted(
        (
            {
                "family": family,
                "label": _family_label(family),
                "config_keys": sorted(keys),
                "count": len(keys),
            }
            for family, keys in families.items()
        ),
        key=lambda item: (-int(item.get("count") or 0), str(item.get("family") or "")),
    )
    return {
        "changed_config_keys": [str(item.get("config_key") or "") for item in entries if item.get("config_key")],
        "changed_config_entries": entries,
        "changed_config_families": ordered_families,
        "primary_family": ordered_families[0]["family"] if ordered_families else "",
    }


def _verification_attribution(
    changed_config: dict[str, Any],
    pre_metrics: dict[str, Any],
    post_metrics: dict[str, Any],
    deltas: dict[str, Any],
    post_outcomes: dict[str, Any],
) -> dict[str, Any]:
    families = changed_config.get("changed_config_families") if isinstance(changed_config.get("changed_config_families"), list) else []
    notes: list[str] = []
    actions: list[str] = []

    send_delta = float(deltas.get("send_rate_delta") or 0.0)
    skip_delta = float(deltas.get("skip_pressure_delta") or 0.0)
    block_delta = float(deltas.get("block_pressure_delta") or 0.0)
    win_rate = float(post_outcomes.get("win_rate") or 0.0)
    fail_rate = float(post_outcomes.get("fail_rate") or 0.0)
    post_total = int(post_outcomes.get("total") or 0)

    family_names = {str(item.get("family") or "") for item in families}

    if "candidate_attention" in family_names or "candidate_timing" in family_names:
        if skip_delta < 0:
            notes.append(f"Candidate-admission pressure improved after changing {_family_label('candidate_attention' if 'candidate_attention' in family_names else 'candidate_timing')}.")
            actions.append("candidate_admission_relief")
        elif skip_delta > 0:
            notes.append("Candidate-admission pressure increased after the rollout, which suggests tighter early gating.")
            actions.append("candidate_admission_tighter")

    if "market_quality" in family_names or "promotion_quality" in family_names:
        if block_delta < 0:
            notes.append("Promotion friction eased on the changed market/promotion quality gates.")
            actions.append("promotion_friction_down")
        elif block_delta > 0:
            notes.append("Promotion blocking increased on the changed market/promotion quality gates.")
            actions.append("promotion_friction_up")

    if "promotion_risk" in family_names:
        if post_total >= 3 and win_rate >= 50.0:
            notes.append("Risk-oriented changes held up in realized post-rollout outcomes.")
            actions.append("risk_gate_constructive")
        elif post_total >= 3 and fail_rate >= 50.0:
            notes.append("Risk-oriented changes degraded realized post-rollout outcomes.")
            actions.append("risk_gate_weak")

    if send_delta > 0 and not notes:
        notes.append("Send-through improved after the rollout, but the changed keys span multiple threshold families.")
        actions.append("send_through_up")
    elif send_delta < 0 and not notes:
        notes.append("Send-through declined after the rollout, which suggests the updated thresholds are more restrictive.")
        actions.append("send_through_down")

    if not notes and not families:
        notes.append("No changed config keys were captured for this approval, so attribution is limited to generic pre/post deltas.")
        actions.append("no_changed_keys")

    family_summary = ", ".join(str(item.get("label") or "") for item in families[:3])
    summary = notes[0] if notes else "No attribution insight available."
    if family_summary:
        summary = f"{summary} Focus: {family_summary}."

    return {
        "summary": summary,
        "notes": notes,
        "signals": actions,
        "pre_total_decisions": int(pre_metrics.get("total_decisions") or 0),
        "post_total_decisions": int(post_metrics.get("total_decisions") or 0),
    }


def _normalize_approval(row: Any) -> dict[str, Any]:
    payload = _safe_json_loads(row["payload_json"], {})
    return {
        "approval_id": row["approval_id"],
        "created_ts": row["created_ts"],
        "approved_by": row["approved_by"],
        "approval_kind": row["approval_kind"],
        "target_name": row["target_name"],
        "artifact_kind": row["artifact_kind"],
        "lookback_hours": row["lookback_hours"],
        "rollout_status": row["rollout_status"] or "pending",
        "rolled_out_ts": row["rolled_out_ts"],
        "deployment_service": row["deployment_service"] or "",
        "deployment_sha": row["deployment_sha"] or "",
        "deployment_env": row["deployment_env"] or "",
        "verification_status": row["verification_status"] or "",
        "verification_ts": row["verification_ts"],
        "verification_summary": row["verification_summary"] or "",
        "notes": row["notes"] or "",
        "artifact_text": row["artifact_text"],
        "payload": payload,
    }


def _latest_rolled_out_profile_for_service(target_name: str, service: str) -> dict[str, Any] | None:
    approvals = list_tuning_approvals(
        limit=50,
        approval_kind="profile",
        artifact_kind="env",
        target_name=target_name,
        rollout_status="rolled_out",
    )
    for approval in approvals:
        if str(approval.get("deployment_service") or "") == service:
            return approval
    return None


def _approvals_aligned(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    return (
        str(left.get("artifact_text") or "") == str(right.get("artifact_text") or "")
        and str(left.get("deployment_sha") or "") == str(right.get("deployment_sha") or "")
    )


def _rollout_notifications(alignment: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = _required_aligned_profiles()
    notifications: list[dict[str, Any]] = []
    for item in alignment:
        target = str(item.get("target_name") or "")
        worker_present = bool(item.get("worker_approval_id"))
        engine_present = bool(item.get("engine_approval_id"))
        aligned = bool(item.get("aligned"))
        if target in required and worker_present and engine_present and not aligned:
            notifications.append(
                {
                    "level": "warning",
                    "code": "required_profile_misaligned",
                    "message": f"{target} is required to align across worker and engine, but the rolled-out approvals differ.",
                    "target_name": target,
                }
            )
        elif worker_present and engine_present and aligned:
            notifications.append(
                {
                    "level": "info",
                    "code": "profile_aligned",
                    "message": f"{target} is aligned across worker and engine.",
                    "target_name": target,
                }
            )
        elif worker_present ^ engine_present:
            notifications.append(
                {
                    "level": "warning",
                    "code": "partial_rollout",
                    "message": f"{target} is rolled out on only one service.",
                    "target_name": target,
                }
            )
    return notifications


def _rollout_recommendations(alignment: list[dict[str, Any]]) -> list[str]:
    recommendations: list[str] = []
    required = _required_aligned_profiles()
    for item in alignment:
        target = str(item.get("target_name") or "")
        if target in required and not bool(item.get("aligned")):
            recommendations.append(f"Block further {target} profile rollout until worker and engine use the same approval and SHA.")
        elif bool(item.get("worker_approval_id")) and not bool(item.get("engine_approval_id")):
            recommendations.append(f"Deploy the latest {target} profile to engine to complete the rollout.")
        elif bool(item.get("engine_approval_id")) and not bool(item.get("worker_approval_id")):
            recommendations.append(f"Deploy the latest {target} profile to worker to complete the rollout.")
    if not recommendations:
        recommendations.append("No rollout action required. Current worker/engine profile state is aligned.")
    return recommendations


def _record_rollout_notification(
    *,
    event_type: str,
    level: str,
    message: str,
    target_name: str | None = None,
    approval_id: str | None = None,
    deployment_service: str | None = None,
    deployment_sha: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    notification = {
        "notification_id": f"roll-{uuid.uuid4().hex[:12]}",
        "created_ts": int(time.time()),
        "event_type": str(event_type or "unknown"),
        "level": str(level or "info"),
        "target_name": (target_name or "").strip() or None,
        "approval_id": (approval_id or "").strip() or None,
        "deployment_service": (deployment_service or "").strip() or None,
        "deployment_sha": (deployment_sha or "").strip() or None,
        "message": str(message or "").strip(),
        "payload": payload or {},
        "delivery_status": "pending",
        "delivered_ts": None,
        "last_error": "",
        "acknowledged_ts": None,
        "acknowledged_by": "",
        "snoozed_until_ts": None,
        "resolved_ts": None,
        "resolved_by": "",
        "resolution_note": "",
    }
    with sls._connect() as c:
        c.execute(
            """
            INSERT INTO rollout_notifications (
                notification_id, created_ts, event_type, level, target_name, approval_id,
                deployment_service, deployment_sha, message, payload_json, delivery_status,
                delivered_ts, last_error, acknowledged_ts, acknowledged_by, snoozed_until_ts,
                resolved_ts, resolved_by, resolution_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification["notification_id"],
                notification["created_ts"],
                notification["event_type"],
                notification["level"],
                notification["target_name"],
                notification["approval_id"],
                notification["deployment_service"],
                notification["deployment_sha"],
                notification["message"],
                json.dumps(notification["payload"]),
                notification["delivery_status"],
                notification["delivered_ts"],
                notification["last_error"],
                notification["acknowledged_ts"],
                notification["acknowledged_by"],
                notification["snoozed_until_ts"],
                notification["resolved_ts"],
                notification["resolved_by"],
                notification["resolution_note"],
            ),
        )
    return notification


def _deliver_rollout_notification(notification: dict[str, Any]) -> dict[str, Any]:
    webhook_url = _ops_webhook_url()
    if not webhook_url:
        with sls._connect() as c:
            c.execute(
                "UPDATE rollout_notifications SET delivery_status=?, last_error=? WHERE notification_id=?",
                ("disabled", "ops_webhook_not_configured", notification["notification_id"]),
            )
        notification["delivery_status"] = "disabled"
        notification["last_error"] = "ops_webhook_not_configured"
        return notification

    try:
        response = requests.post(
            webhook_url,
            json={
                "event_type": notification["event_type"],
                "level": notification["level"],
                "target_name": notification["target_name"],
                "approval_id": notification["approval_id"],
                "deployment_service": notification["deployment_service"],
                "deployment_sha": notification["deployment_sha"],
                "message": notification["message"],
                "payload": notification["payload"],
            },
            timeout=10,
        )
        response.raise_for_status()
        delivered_ts = int(time.time())
        with sls._connect() as c:
            c.execute(
                "UPDATE rollout_notifications SET delivery_status=?, delivered_ts=?, last_error=? WHERE notification_id=?",
                ("delivered", delivered_ts, "", notification["notification_id"]),
            )
        notification["delivery_status"] = "delivered"
        notification["delivered_ts"] = delivered_ts
        notification["last_error"] = ""
        return notification
    except Exception as exc:
        error_text = str(exc)
        with sls._connect() as c:
            c.execute(
                "UPDATE rollout_notifications SET delivery_status=?, last_error=? WHERE notification_id=?",
                ("failed", error_text[:500], notification["notification_id"]),
            )
        notification["delivery_status"] = "failed"
        notification["last_error"] = error_text[:500]
        return notification


def emit_rollout_notification(
    *,
    event_type: str,
    level: str,
    message: str,
    target_name: str | None = None,
    approval_id: str | None = None,
    deployment_service: str | None = None,
    deployment_sha: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    notification = _record_rollout_notification(
        event_type=event_type,
        level=level,
        message=message,
        target_name=target_name,
        approval_id=approval_id,
        deployment_service=deployment_service,
        deployment_sha=deployment_sha,
        payload=payload,
    )
    return _deliver_rollout_notification(notification)


def _notification_active(row: dict[str, Any], now_ts: int | None = None) -> bool:
    current_ts = int(now_ts or time.time())
    acknowledged = bool(row.get("acknowledged_ts"))
    snoozed_until = int(row.get("snoozed_until_ts") or 0)
    resolved = bool(row.get("resolved_ts"))
    return (not acknowledged) and (not resolved) and (snoozed_until <= current_ts)


def _incident_state(notifications: list[dict[str, Any]], now_ts: int | None = None) -> str:
    current_ts = int(now_ts or time.time())
    if not notifications:
        return "resolved"
    if any(_notification_active(item, current_ts) for item in notifications):
        return "open"
    if any(int(item.get("snoozed_until_ts") or 0) > current_ts and not item.get("resolved_ts") for item in notifications):
        return "snoozed"
    if all(bool(item.get("resolved_ts")) for item in notifications):
        return "resolved"
    if any(bool(item.get("acknowledged_ts")) for item in notifications):
        return "acknowledged"
    return "resolved"


def _notification_cluster_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row.get("event_type") or "unknown"),
            str(row.get("target_name") or ""),
            str(row.get("deployment_service") or ""),
        ]
    )


def list_notification_incidents(limit: int = 20, *, active_only: bool = False) -> list[dict[str, Any]]:
    current_ts = int(time.time())
    notifications = list_rollout_notifications(limit=max(1, limit * 10), active_only=active_only)
    clusters: dict[str, dict[str, Any]] = {}
    for item in notifications:
        key = _notification_cluster_key(item)
        cluster = clusters.get(key)
        if cluster is None:
            cluster = {
                "incident_key": key,
                "event_type": item.get("event_type") or "unknown",
                "target_name": item.get("target_name") or "",
                "deployment_service": item.get("deployment_service") or "",
                "level": item.get("level") or "info",
                "count": 0,
                "first_seen_ts": item.get("created_ts"),
                "last_seen_ts": item.get("created_ts"),
                "latest_message": item.get("message") or "",
                "latest_notification_id": item.get("notification_id") or "",
                "delivery_status": item.get("delivery_status") or "pending",
                "active": False,
                "state": "open",
                "acknowledged_count": 0,
                "snoozed_count": 0,
                "resolved_count": 0,
                "time_to_first_ack_seconds": None,
                "time_to_resolve_seconds": None,
                "resolved_by": "",
                "resolution_note": "",
                "notifications": [],
            }
            clusters[key] = cluster

        cluster["count"] = int(cluster["count"]) + 1
        cluster["first_seen_ts"] = min(int(cluster["first_seen_ts"] or item.get("created_ts") or 0), int(item.get("created_ts") or 0))
        cluster["last_seen_ts"] = max(int(cluster["last_seen_ts"] or item.get("created_ts") or 0), int(item.get("created_ts") or 0))
        if int(item.get("created_ts") or 0) >= int(cluster.get("last_seen_ts") or 0):
            cluster["latest_message"] = item.get("message") or ""
            cluster["latest_notification_id"] = item.get("notification_id") or ""
            cluster["delivery_status"] = item.get("delivery_status") or "pending"
            cluster["level"] = item.get("level") or cluster["level"]
        if bool(item.get("acknowledged_ts")):
            cluster["acknowledged_count"] = int(cluster["acknowledged_count"]) + 1
        if int(item.get("snoozed_until_ts") or 0) > current_ts:
            cluster["snoozed_count"] = int(cluster["snoozed_count"]) + 1
        if bool(item.get("resolved_ts")):
            cluster["resolved_count"] = int(cluster["resolved_count"]) + 1
        if _notification_active(item):
            cluster["active"] = True
        cluster["notifications"].append(item)

    for cluster in clusters.values():
        cluster_notifications = cluster["notifications"]
        cluster["state"] = _incident_state(cluster_notifications, current_ts)
        ack_ts_values = [int(item["acknowledged_ts"]) for item in cluster_notifications if item.get("acknowledged_ts")]
        if ack_ts_values:
            cluster["time_to_first_ack_seconds"] = max(0, min(ack_ts_values) - int(cluster["first_seen_ts"] or 0))
        resolved_ts_values = [int(item["resolved_ts"]) for item in cluster_notifications if item.get("resolved_ts")]
        if resolved_ts_values:
            cluster["time_to_resolve_seconds"] = max(0, max(resolved_ts_values) - int(cluster["first_seen_ts"] or 0))
            latest_resolved = max(
                (item for item in cluster_notifications if item.get("resolved_ts")),
                key=lambda item: int(item.get("resolved_ts") or 0),
            )
            cluster["resolved_by"] = latest_resolved.get("resolved_by") or ""
            cluster["resolution_note"] = latest_resolved.get("resolution_note") or ""

    incidents = sorted(
        clusters.values(),
        key=lambda item: (int(item.get("active") or 0), int(item.get("last_seen_ts") or 0)),
        reverse=True,
    )
    return incidents[: max(1, limit)]


def update_incident_state(
    *,
    event_type: str,
    target_name: str | None = None,
    deployment_service: str | None = None,
    acknowledged: bool | None = None,
    acknowledged_by: str | None = None,
    snooze_minutes: int | None = None,
    unsnooze: bool = False,
    resolved: bool | None = None,
    resolved_by: str | None = None,
    resolution_note: str | None = None,
) -> dict[str, Any]:
    target_event = str(event_type or "").strip()
    if not target_event:
        raise ValueError("event_type_required")
    target_name_value = str(target_name or "").strip()
    target_service_value = str(deployment_service or "").strip()

    notifications = list_rollout_notifications(limit=500)
    matched = [
        item
        for item in notifications
        if str(item.get("event_type") or "") == target_event
        and str(item.get("target_name") or "") == target_name_value
        and str(item.get("deployment_service") or "") == target_service_value
    ]
    if not matched:
        raise KeyError(f"{target_event}|{target_name_value}|{target_service_value}")

    for item in matched:
        update_rollout_notification_state(
            str(item["notification_id"]),
            acknowledged=acknowledged,
            acknowledged_by=acknowledged_by,
            snooze_minutes=snooze_minutes,
            unsnooze=unsnooze,
            resolved=resolved,
            resolved_by=resolved_by,
            resolution_note=resolution_note,
        )

    incidents = list_notification_incidents(limit=200)
    incident_key = "|".join([target_event, target_name_value, target_service_value])
    for incident in incidents:
        if incident["incident_key"] == incident_key:
            return incident
    raise KeyError(incident_key)


def list_rollout_notifications(limit: int = 20, *, active_only: bool = False) -> list[dict[str, Any]]:
    with sls._connect() as c:
        rows = c.execute(
            """
            SELECT notification_id, created_ts, event_type, level, target_name, approval_id,
                   deployment_service, deployment_sha, message, payload_json, delivery_status,
                   delivered_ts, last_error, acknowledged_ts, acknowledged_by, snoozed_until_ts,
                   resolved_ts, resolved_by, resolution_note
            FROM rollout_notifications
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    notifications = [
        {
            "notification_id": row["notification_id"],
            "created_ts": row["created_ts"],
            "event_type": row["event_type"],
            "level": row["level"],
            "target_name": row["target_name"],
            "approval_id": row["approval_id"],
            "deployment_service": row["deployment_service"] or "",
            "deployment_sha": row["deployment_sha"] or "",
            "message": row["message"],
            "payload": _safe_json_loads(row["payload_json"], {}),
            "delivery_status": row["delivery_status"] or "pending",
            "delivered_ts": row["delivered_ts"],
            "last_error": row["last_error"] or "",
            "acknowledged_ts": row["acknowledged_ts"],
            "acknowledged_by": row["acknowledged_by"] or "",
            "snoozed_until_ts": row["snoozed_until_ts"],
            "resolved_ts": row["resolved_ts"],
            "resolved_by": row["resolved_by"] or "",
            "resolution_note": row["resolution_note"] or "",
        }
        for row in rows
    ]
    if active_only:
        current_ts = int(time.time())
        notifications = [item for item in notifications if _notification_active(item, current_ts)]
    return notifications


def _latest_rollout_notification(event_type: str) -> dict[str, Any] | None:
    with sls._connect() as c:
        row = c.execute(
            """
            SELECT notification_id, created_ts, event_type, level, target_name, approval_id,
                   deployment_service, deployment_sha, message, payload_json, delivery_status,
                   delivered_ts, last_error, acknowledged_ts, acknowledged_by, snoozed_until_ts,
                   resolved_ts, resolved_by, resolution_note
            FROM rollout_notifications
            WHERE event_type=?
            ORDER BY created_ts DESC
            LIMIT 1
            """,
            (str(event_type or ""),),
        ).fetchone()
    if row is None:
        return None
    payload = _safe_json_loads(row["payload_json"], {})
    return {
        "notification_id": row["notification_id"],
        "created_ts": row["created_ts"],
        "event_type": row["event_type"],
        "level": row["level"],
        "target_name": row["target_name"] or "",
        "approval_id": row["approval_id"] or "",
        "deployment_service": row["deployment_service"] or "",
        "deployment_sha": row["deployment_sha"] or "",
        "message": row["message"] or "",
        "payload": payload,
        "delivery_status": row["delivery_status"] or "pending",
        "delivered_ts": row["delivered_ts"],
        "last_error": row["last_error"] or "",
        "acknowledged_ts": row["acknowledged_ts"],
        "acknowledged_by": row["acknowledged_by"] or "",
        "snoozed_until_ts": row["snoozed_until_ts"],
        "resolved_ts": row["resolved_ts"],
        "resolved_by": row["resolved_by"] or "",
        "resolution_note": row["resolution_note"] or "",
    }


def update_rollout_notification_state(
    notification_id: str,
    *,
    acknowledged: bool | None = None,
    acknowledged_by: str | None = None,
    snooze_minutes: int | None = None,
    unsnooze: bool = False,
    resolved: bool | None = None,
    resolved_by: str | None = None,
    resolution_note: str | None = None,
) -> dict[str, Any]:
    target_id = str(notification_id or "").strip()
    if not target_id:
        raise ValueError("notification_id_required")

    with sls._connect() as c:
        row = c.execute(
            """
            SELECT notification_id FROM rollout_notifications WHERE notification_id=?
            """,
            (target_id,),
        ).fetchone()
        if row is None:
            raise KeyError(target_id)

        updates: list[str] = []
        params: list[Any] = []

        if acknowledged is not None:
            updates.append("acknowledged_ts=?")
            params.append(int(time.time()) if acknowledged else None)
            updates.append("acknowledged_by=?")
            params.append(str(acknowledged_by or "").strip() if acknowledged else "")
            if acknowledged:
                updates.append("snoozed_until_ts=?")
                params.append(None)

        if snooze_minutes is not None:
            updates.append("snoozed_until_ts=?")
            params.append(int(time.time()) + max(1, int(snooze_minutes)) * 60)
        elif unsnooze:
            updates.append("snoozed_until_ts=?")
            params.append(None)

        if resolved is not None:
            updates.append("resolved_ts=?")
            params.append(int(time.time()) if resolved else None)
            updates.append("resolved_by=?")
            params.append(str(resolved_by or "").strip() if resolved else "")
            updates.append("resolution_note=?")
            params.append(str(resolution_note or "").strip() if resolved else "")

        if not updates:
            raise ValueError("no_notification_state_change")

        params.append(target_id)
        c.execute(
            f"UPDATE rollout_notifications SET {', '.join(updates)} WHERE notification_id=?",
            tuple(params),
        )

    updated = list_rollout_notifications(limit=200)
    for item in updated:
        if item["notification_id"] == target_id:
            item["active"] = _notification_active(item)
            return item
    raise KeyError(target_id)


def _ops_digest_cooldown_seconds() -> int:
    raw = os.getenv("SIGNAL_ENGINE_OPS_DIGEST_COOLDOWN_SEC", "").strip()
    try:
        value = int(raw) if raw else 3600
    except ValueError:
        value = 3600
    return max(60, value)


def _ops_digest_poll_seconds() -> int:
    raw = os.getenv("SIGNAL_ENGINE_OPS_DIGEST_POLL_SEC", "").strip()
    try:
        value = int(raw) if raw else 900
    except ValueError:
        value = 900
    return max(60, value)


def _ops_digest_default_hours() -> int:
    raw = os.getenv("SIGNAL_ENGINE_OPS_DIGEST_HOURS", "").strip()
    try:
        value = int(raw) if raw else 24
    except ValueError:
        value = 24
    return max(1, value)


def _ops_daily_summary_hours() -> int:
    raw = os.getenv("SIGNAL_ENGINE_OPS_DAILY_SUMMARY_HOURS", "").strip()
    try:
        value = int(raw) if raw else 24
    except ValueError:
        value = 24
    return max(1, value)


def _rollout_verification_poll_seconds() -> int:
    raw = os.getenv("SIGNAL_ENGINE_ROLLOUT_VERIFY_POLL_SEC", "").strip()
    try:
        value = int(raw) if raw else 1800
    except ValueError:
        value = 1800
    return max(60, value)


def _rollout_verification_min_age_seconds() -> int:
    raw = os.getenv("SIGNAL_ENGINE_ROLLOUT_VERIFY_MIN_AGE_SEC", "").strip()
    try:
        value = int(raw) if raw else 3600
    except ValueError:
        value = 3600
    return max(300, value)


def _ops_threshold_float(env_key: str, default: float) -> float:
    raw = os.getenv(env_key, "").strip()
    try:
        return float(raw) if raw else float(default)
    except ValueError:
        return float(default)


def _ops_threshold_int(env_key: str, default: int) -> int:
    raw = os.getenv(env_key, "").strip()
    try:
        return int(raw) if raw else int(default)
    except ValueError:
        return int(default)


def _ops_digest_policy() -> dict[str, float | int]:
    return {
        "degraded_skip_pressure": _ops_threshold_float("SIGNAL_ENGINE_OPS_DEGRADED_SKIP_PRESSURE", 70.0),
        "incident_skip_pressure": _ops_threshold_float("SIGNAL_ENGINE_OPS_INCIDENT_SKIP_PRESSURE", 85.0),
        "degraded_block_pressure": _ops_threshold_float("SIGNAL_ENGINE_OPS_DEGRADED_BLOCK_PRESSURE", 35.0),
        "incident_block_pressure": _ops_threshold_float("SIGNAL_ENGINE_OPS_INCIDENT_BLOCK_PRESSURE", 50.0),
        "incident_notification_count": _ops_threshold_int("SIGNAL_ENGINE_OPS_INCIDENT_NOTIFICATION_COUNT", 2),
        "critical_drift_profiles": _ops_threshold_int("SIGNAL_ENGINE_OPS_CRITICAL_DRIFT_PROFILES", 2),
        "incident_zero_send_min_skips": _ops_threshold_int("SIGNAL_ENGINE_OPS_INCIDENT_ZERO_SEND_MIN_SKIPS", 10),
        "degraded_reminder_sec": _ops_threshold_int("SIGNAL_ENGINE_OPS_DEGRADED_REMINDER_SEC", 14400),
        "daily_summary_interval_sec": _ops_threshold_int("SIGNAL_ENGINE_OPS_DAILY_SUMMARY_INTERVAL_SEC", 86400),
    }


def _ops_digest_signature(digest: dict[str, Any]) -> str:
    payload = {
        "severity": digest.get("severity"),
        "needs_attention": digest.get("needs_attention"),
        "attention_reasons": digest.get("attention_reasons") or [],
        "summary": digest.get("summary"),
        "top_skip_reason": digest.get("top_skip_reason"),
        "drift_profiles": digest.get("drift_profiles") or [],
        "counts": digest.get("counts") or {},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _typed_ops_digest_signature(digest_type: str, digest: dict[str, Any]) -> str:
    return json.dumps(
        {
            "digest_type": str(digest_type or "ops_digest"),
            "digest_signature": _ops_digest_signature(digest),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _is_ops_digest_event_type(event_type: str | None) -> bool:
    value = str(event_type or "").strip().lower()
    return value in {"ops_digest", "incident_digest", "degraded_digest", "daily_summary"}


def _merge_family_scorecards(
    primary: list[dict[str, Any]] | None,
    secondary: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for collection in (primary or [], secondary or []):
        for item in collection:
            if not isinstance(item, dict):
                continue
            family = str(item.get("family") or "").strip()
            key = family or str(item.get("label") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def render_rollout_notifications_html(limit: int = 20, *, active_only: bool = False) -> str:
    notifications = list_rollout_notifications(limit=max(1, limit), active_only=active_only)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('event_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('level') or 'info'))}</td>"
        f"<td>{html.escape(str(item.get('target_name') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('deployment_service') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('delivery_status') or 'pending'))}</td>"
        f"<td>{'yes' if _notification_active(item) else 'no'}</td>"
        f"<td>{html.escape(str(item.get('message') or ''))}</td>"
        "</tr>"
        for item in notifications
    ) or "<tr><td colspan='7'>No rollout notifications recorded yet.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rollout Notifications</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    body {{ margin: 0; color: var(--text); font-family: "Segoe UI", sans-serif; background: linear-gradient(180deg, #071018 0%, #09131c 100%); }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; margin-top: 18px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    p {{ margin: 0 0 12px; color: var(--muted); }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Rollout Notifications</h1>
      <p>Policy and rollout events emitted from the tuning lifecycle. Filtered to {"active only" if active_only else "recent history"}.</p>
      <table>
        <thead><tr><th>Event</th><th>Level</th><th>Target</th><th>Service</th><th>Delivery</th><th>Active</th><th>Message</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def render_notification_incidents_html(limit: int = 20, *, active_only: bool = False) -> str:
    incidents = list_notification_incidents(limit=max(1, limit), active_only=active_only)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('event_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('target_name') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('deployment_service') or 'n/a'))}</td>"
        f"<td>{int(item.get('count') or 0)}</td>"
        f"<td>{html.escape(str(item.get('state') or 'open'))}</td>"
        f"<td>{'yes' if item.get('active') else 'no'}</td>"
        f"<td>{html.escape(str(item.get('time_to_first_ack_seconds') or '-'))}</td>"
        f"<td>{html.escape(str(item.get('time_to_resolve_seconds') or '-'))}</td>"
        f"<td>{html.escape(str(item.get('latest_message') or ''))}</td>"
        "</tr>"
        for item in incidents
    ) or "<tr><td colspan='9'>No incident clusters recorded yet.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Notification Incidents</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--text); font-family: "Segoe UI", sans-serif; background: linear-gradient(180deg, #071018 0%, #09131c 100%); }}
    .shell {{ max-width: 1280px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; margin-top: 18px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Notification Incidents</h1>
      <p>Clustered notification view by event, target, and service. Filtered to {"active only" if active_only else "recent history"}.</p>
      <table>
        <thead><tr><th>Event</th><th>Target</th><th>Service</th><th>Count</th><th>State</th><th>Active</th><th>TTA Ack</th><th>TTR</th><th>Latest Message</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def _decision_metrics_between(start_ts: int, end_ts: int) -> dict[str, Any]:
    with sls._connect() as c:
        rows = c.execute(
            """
            SELECT decision
            FROM signal_decisions
            WHERE created_ts >= ? AND created_ts < ?
            """,
            (int(start_ts), int(end_ts)),
        ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        decision = str(row["decision"] or "unknown")
        counts[decision] = counts.get(decision, 0) + 1
    sent = sum(int(value or 0) for key, value in counts.items() if str(key).endswith("sent"))
    skipped = sum(int(value or 0) for key, value in counts.items() if "skip" in str(key))
    blocked = sum(int(value or 0) for key, value in counts.items() if "block" in str(key))
    total = sum(int(value or 0) for value in counts.values())
    return {
        "window_start_ts": int(start_ts),
        "window_end_ts": int(end_ts),
        "counts_by_decision": counts,
        "total_decisions": total,
        "sent": sent,
        "skipped": skipped,
        "blocked": blocked,
        "send_rate": round((sent / total) * 100.0, 1) if total else 0.0,
        "skip_pressure": round((skipped / total) * 100.0, 1) if total else 0.0,
        "block_pressure": round((blocked / total) * 100.0, 1) if total else 0.0,
    }


def _outcome_metrics_between(start_ts: int, end_ts: int) -> dict[str, Any]:
    with sls._connect() as c:
        rows = c.execute(
            """
            SELECT COALESCE(ss.outcome_label, 'pending') AS outcome_label
            FROM signals s
            LEFT JOIN (
                SELECT signal_id, outcome_label, MAX(horizon_minutes) AS max_horizon
                FROM signal_snapshots
                GROUP BY signal_id
            ) ss ON ss.signal_id = s.signal_id
            WHERE s.alert_ts >= ? AND s.alert_ts < ?
            """,
            (int(start_ts), int(end_ts)),
        ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row["outcome_label"] or "pending")
        counts[label] = counts.get(label, 0) + 1
    positive = int(counts.get("worked") or 0) + int(counts.get("strong_continuation") or 0)
    negative = int(counts.get("failed") or 0)
    total = sum(int(value or 0) for value in counts.values())
    return {
        "outcomes_by_label": counts,
        "total": total,
        "positive": positive,
        "negative": negative,
        "win_rate": round((positive / total) * 100.0, 1) if total else 0.0,
        "fail_rate": round((negative / total) * 100.0, 1) if total else 0.0,
    }


def _observed_data_end_ts() -> int:
    with sls._connect() as c:
        decision_row = c.execute("SELECT MAX(created_ts) AS max_ts FROM signal_decisions").fetchone()
        signal_row = c.execute("SELECT MAX(alert_ts) AS max_ts FROM signals").fetchone()
    decision_ts = int((decision_row["max_ts"] if decision_row else 0) or 0)
    signal_ts = int((signal_row["max_ts"] if signal_row else 0) or 0)
    return max(decision_ts, signal_ts) + 1


def _latest_matching_rollout(
    *,
    target_name: str | None = None,
    deployment_service: str | None = None,
) -> dict[str, Any] | None:
    approvals = list_tuning_approvals(
        limit=100,
        approval_kind="profile",
        artifact_kind="env",
        target_name=target_name,
        rollout_status="rolled_out",
    )
    if deployment_service:
        target_service = str(deployment_service or "").strip()
        approvals = [item for item in approvals if str(item.get("deployment_service") or "") == target_service]
    return approvals[0] if approvals else None


def _verification_status(pre: dict[str, Any], post: dict[str, Any], post_outcomes: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    improved = 0
    degraded = 0

    if float(post.get("send_rate") or 0.0) > float(pre.get("send_rate") or 0.0):
        improved += 1
        reasons.append("send_rate_up")
    elif float(post.get("send_rate") or 0.0) < float(pre.get("send_rate") or 0.0):
        degraded += 1
        reasons.append("send_rate_down")

    if float(post.get("skip_pressure") or 0.0) < float(pre.get("skip_pressure") or 0.0):
        improved += 1
        reasons.append("skip_pressure_down")
    elif float(post.get("skip_pressure") or 0.0) > float(pre.get("skip_pressure") or 0.0):
        degraded += 1
        reasons.append("skip_pressure_up")

    if float(post.get("block_pressure") or 0.0) < float(pre.get("block_pressure") or 0.0):
        improved += 1
        reasons.append("block_pressure_down")
    elif float(post.get("block_pressure") or 0.0) > float(pre.get("block_pressure") or 0.0):
        degraded += 1
        reasons.append("block_pressure_up")

    if int(post_outcomes.get("total") or 0) >= 3:
        if float(post_outcomes.get("win_rate") or 0.0) >= 50.0:
            improved += 1
            reasons.append("post_rollout_outcomes_constructive")
        elif float(post_outcomes.get("fail_rate") or 0.0) >= 50.0:
            degraded += 1
            reasons.append("post_rollout_outcomes_weak")

    if int(pre.get("total_decisions") or 0) == 0 and int(post.get("total_decisions") or 0) == 0:
        return "insufficient_data", ["no_decision_data"]
    if improved > degraded:
        return "improved", reasons
    if degraded > improved:
        return "degraded", reasons
    return "mixed", reasons or ["balanced_change"]


def _build_rollout_verification_payload(
    approval: dict[str, Any],
    *,
    baseline_hours: int,
    post_hours: int,
    include_family_scorecards: bool,
) -> dict[str, Any]:
    rollout_ts = int(approval.get("rolled_out_ts") or approval.get("created_ts") or 0)
    baseline_start = rollout_ts - max(1, int(baseline_hours)) * 3600
    observed_end = max(int(time.time()), _observed_data_end_ts())
    post_end = min(observed_end, rollout_ts + max(1, int(post_hours)) * 3600)

    pre_metrics = _decision_metrics_between(baseline_start, rollout_ts)
    post_metrics = _decision_metrics_between(rollout_ts, post_end)
    post_outcomes = _outcome_metrics_between(rollout_ts, post_end)
    status, reasons = _verification_status(pre_metrics, post_metrics, post_outcomes)
    changed_config = _summarize_changed_config(_changed_config_entries(approval))

    deltas = {
        "send_rate_delta": round(float(post_metrics.get("send_rate") or 0.0) - float(pre_metrics.get("send_rate") or 0.0), 1),
        "skip_pressure_delta": round(float(post_metrics.get("skip_pressure") or 0.0) - float(pre_metrics.get("skip_pressure") or 0.0), 1),
        "block_pressure_delta": round(float(post_metrics.get("block_pressure") or 0.0) - float(pre_metrics.get("block_pressure") or 0.0), 1),
    }
    attribution = _verification_attribution(changed_config, pre_metrics, post_metrics, deltas, post_outcomes)
    drift = (
        get_config_drift_report(target_name=str(approval.get("target_name") or ""), rollout_status="rolled_out")
        if str(approval.get("target_name") or "") in {"strict", "balanced", "aggressive"}
        else None
    )

    payload = {
        "approval": approval,
        "baseline_hours": int(baseline_hours),
        "post_hours": int(post_hours),
        "rollout_ts": rollout_ts,
        "pre_metrics": pre_metrics,
        "post_metrics": post_metrics,
        "post_outcomes": post_outcomes,
        "deltas": deltas,
        "verification_status": status,
        "verification_reasons": reasons,
        "changed_config": changed_config,
        "attribution": attribution,
        "drift": drift,
    }
    if include_family_scorecards:
        payload["family_scorecards"] = _rollout_family_scorecards(
            baseline_hours=baseline_hours,
            post_hours=post_hours,
            focus_families=[
                str(item.get("family") or "")
                for item in (changed_config.get("changed_config_families") if isinstance(changed_config.get("changed_config_families"), list) else [])
                if isinstance(item, dict)
            ],
        )
    return payload


def _rollout_family_scorecards(
    *,
    baseline_hours: int,
    post_hours: int,
    focus_families: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    approvals = list_tuning_approvals(limit=max(1, limit), rollout_status="rolled_out")
    aggregates: dict[str, dict[str, Any]] = {}
    focus_set = {str(item).strip() for item in (focus_families or []) if str(item).strip()}

    for approval in approvals:
        try:
            verification = _build_rollout_verification_payload(
                approval,
                baseline_hours=baseline_hours,
                post_hours=post_hours,
                include_family_scorecards=False,
            )
        except Exception as exc:
            logger.warning(
                "[rollout-family-scorecards] skipping approval_id=%s due to verification error: %s",
                approval.get("approval_id"),
                exc,
            )
            continue
        families = verification.get("changed_config", {}).get("changed_config_families") if isinstance(verification.get("changed_config"), dict) else []
        for family_info in families if isinstance(families, list) else []:
            if not isinstance(family_info, dict):
                continue
            family = str(family_info.get("family") or "").strip()
            if not family:
                continue
            if focus_set and family not in focus_set:
                continue
            entry = aggregates.setdefault(
                family,
                {
                    "family": family,
                    "label": _family_label(family),
                    "rollout_count": 0,
                    "improved_count": 0,
                    "mixed_count": 0,
                    "degraded_count": 0,
                    "insufficient_count": 0,
                    "avg_send_rate_delta": 0.0,
                    "avg_skip_pressure_delta": 0.0,
                    "avg_block_pressure_delta": 0.0,
                    "avg_post_win_rate": 0.0,
                },
            )
            entry["rollout_count"] = int(entry["rollout_count"]) + 1
            status = str(verification.get("verification_status") or "mixed")
            if status == "improved":
                entry["improved_count"] = int(entry["improved_count"]) + 1
            elif status == "degraded":
                entry["degraded_count"] = int(entry["degraded_count"]) + 1
            elif status == "insufficient_data":
                entry["insufficient_count"] = int(entry["insufficient_count"]) + 1
            else:
                entry["mixed_count"] = int(entry["mixed_count"]) + 1

            entry["avg_send_rate_delta"] += float(verification.get("deltas", {}).get("send_rate_delta") or 0.0)
            entry["avg_skip_pressure_delta"] += float(verification.get("deltas", {}).get("skip_pressure_delta") or 0.0)
            entry["avg_block_pressure_delta"] += float(verification.get("deltas", {}).get("block_pressure_delta") or 0.0)
            entry["avg_post_win_rate"] += float(verification.get("post_outcomes", {}).get("win_rate") or 0.0)

    scorecards: list[dict[str, Any]] = []
    for item in aggregates.values():
        count = max(1, int(item["rollout_count"]))
        scorecards.append(
            {
                **item,
                "avg_send_rate_delta": round(float(item["avg_send_rate_delta"]) / count, 1),
                "avg_skip_pressure_delta": round(float(item["avg_skip_pressure_delta"]) / count, 1),
                "avg_block_pressure_delta": round(float(item["avg_block_pressure_delta"]) / count, 1),
                "avg_post_win_rate": round(float(item["avg_post_win_rate"]) / count, 1),
            }
        )

    return sorted(
        scorecards,
        key=lambda item: (
            -int(item.get("rollout_count") or 0),
            -int(item.get("improved_count") or 0),
            str(item.get("family") or ""),
        ),
    )


def _proposal_historical_support(
    *,
    family: str,
    family_scorecards: list[dict[str, Any]],
) -> dict[str, Any]:
    for item in family_scorecards:
        if str(item.get("family") or "") != family:
            continue
        rollout_count = int(item.get("rollout_count") or 0)
        improved_count = int(item.get("improved_count") or 0)
        degraded_count = int(item.get("degraded_count") or 0)
        avg_send_delta = float(item.get("avg_send_rate_delta") or 0.0)
        avg_post_win_rate = float(item.get("avg_post_win_rate") or 0.0)

        support = "neutral"
        if rollout_count >= 2:
            if improved_count > degraded_count and avg_send_delta >= 0 and avg_post_win_rate >= 45.0:
                support = "supportive"
            elif degraded_count > improved_count and (avg_send_delta < 0 or avg_post_win_rate < 40.0):
                support = "caution"

        return {
            "family": family,
            "label": _family_label(family),
            "support": support,
            "rollout_count": rollout_count,
            "improved_count": improved_count,
            "degraded_count": degraded_count,
            "avg_send_rate_delta": avg_send_delta,
            "avg_post_win_rate": avg_post_win_rate,
        }
    return {
        "family": family,
        "label": _family_label(family),
        "support": "unknown",
        "rollout_count": 0,
        "improved_count": 0,
        "degraded_count": 0,
        "avg_send_rate_delta": 0.0,
        "avg_post_win_rate": 0.0,
    }


def _fallback_proposal_historical_support(
    *,
    family: str,
    action: str,
    sample_size: int,
    positive_rate: float,
    fail_rate: float,
) -> dict[str, Any]:
    support = "unknown"
    if sample_size >= 5 and action == "relax_slightly" and positive_rate >= 60.0 and positive_rate >= fail_rate + 20.0:
        support = "supportive"
    elif sample_size >= 5 and action == "tighten" and fail_rate >= 60.0 and fail_rate >= positive_rate + 20.0:
        support = "caution"
    return {
        "family": family,
        "label": _family_label(family),
        "support": support,
        "rollout_count": 0,
        "improved_count": 0,
        "degraded_count": 0,
        "avg_send_rate_delta": 0.0,
        "avg_post_win_rate": round(positive_rate, 1) if support == "supportive" else 0.0,
    }


def _fallback_family_scorecards_from_proposals(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    for item in proposals:
        family = str(item.get("family") or "").strip()
        support_payload = item.get("historical_support") if isinstance(item.get("historical_support"), dict) else {}
        support = str(support_payload.get("support") or "unknown")
        if not family:
            continue
        entry = aggregates.setdefault(
            family,
            {
                "family": family,
                "label": _family_label(family),
                "rollout_count": 0,
                "improved_count": 0,
                "mixed_count": 0,
                "degraded_count": 0,
                "insufficient_count": 0,
                "avg_send_rate_delta": 0.0,
                "avg_skip_pressure_delta": 0.0,
                "avg_block_pressure_delta": 0.0,
                "avg_post_win_rate": 0.0,
            },
        )
        if support == "supportive":
            entry["improved_count"] = int(entry["improved_count"]) + 1
        elif support == "caution":
            entry["degraded_count"] = int(entry["degraded_count"]) + 1
        else:
            entry["insufficient_count"] = int(entry["insufficient_count"]) + 1
        entry["avg_post_win_rate"] += float(support_payload.get("avg_post_win_rate") or 0.0)

    scorecards: list[dict[str, Any]] = []
    for entry in aggregates.values():
        divisor = max(1, int(entry["improved_count"]) + int(entry["degraded_count"]) + int(entry["insufficient_count"]))
        scorecards.append(
            {
                **entry,
                "avg_post_win_rate": round(float(entry["avg_post_win_rate"]) / divisor, 1),
            }
        )
    return sorted(scorecards, key=lambda item: (-int(item.get("improved_count") or 0), str(item.get("family") or "")))


def _proposal_priority(
    *,
    action: str,
    confidence: str,
    sample_size: int,
    historical_support: dict[str, Any],
) -> tuple[str, str]:
    score = 0
    if action == "tighten":
        score += 3
    elif action == "relax_slightly":
        score += 2

    score += {"high": 3, "medium": 2, "low": 1}.get(confidence, 0)
    if sample_size >= 20:
        score += 2
    elif sample_size >= 8:
        score += 1

    support = str(historical_support.get("support") or "unknown")
    if support == "supportive":
        score += 2
    elif support == "caution":
        score -= 2

    if score >= 8:
        priority = "high"
    elif score >= 5:
        priority = "medium"
    else:
        priority = "low"

    summary = (
        f"{historical_support.get('label', 'historical evidence')} is {support}; "
        f"{int(historical_support.get('rollout_count') or 0)} rollouts, "
        f"{float(historical_support.get('avg_post_win_rate') or 0.0)}% avg post win rate."
    )
    return priority, summary


def get_rollout_verification(
    *,
    approval_id: str | None = None,
    target_name: str | None = None,
    deployment_service: str | None = None,
    baseline_hours: int = 24,
    post_hours: int = 24,
) -> dict[str, Any]:
    approval: dict[str, Any] | None = None
    if approval_id:
        for item in list_tuning_approvals(limit=200, rollout_status="rolled_out"):
            if str(item.get("approval_id") or "") == str(approval_id):
                approval = item
                break
    else:
        approval = _latest_matching_rollout(
            target_name=str(target_name or "").strip().lower() or None,
            deployment_service=str(deployment_service or "").strip() or None,
        )
    if approval is None:
        raise KeyError("rollout_not_found")
    return _build_rollout_verification_payload(
        approval,
        baseline_hours=baseline_hours,
        post_hours=post_hours,
        include_family_scorecards=True,
    )


def render_rollout_verification_html(
    *,
    approval_id: str | None = None,
    target_name: str | None = None,
    deployment_service: str | None = None,
    baseline_hours: int = 24,
    post_hours: int = 24,
) -> str:
    verification = get_rollout_verification(
        approval_id=approval_id,
        target_name=target_name,
        deployment_service=deployment_service,
        baseline_hours=baseline_hours,
        post_hours=post_hours,
    )
    approval = verification.get("approval") if isinstance(verification.get("approval"), dict) else {}
    pre = verification.get("pre_metrics") if isinstance(verification.get("pre_metrics"), dict) else {}
    post = verification.get("post_metrics") if isinstance(verification.get("post_metrics"), dict) else {}
    outcomes = verification.get("post_outcomes") if isinstance(verification.get("post_outcomes"), dict) else {}
    deltas = verification.get("deltas") if isinstance(verification.get("deltas"), dict) else {}
    reasons = verification.get("verification_reasons") if isinstance(verification.get("verification_reasons"), list) else []
    changed_config = verification.get("changed_config") if isinstance(verification.get("changed_config"), dict) else {}
    attribution = verification.get("attribution") if isinstance(verification.get("attribution"), dict) else {}
    drift = verification.get("drift") if isinstance(verification.get("drift"), dict) else {}
    family_scorecards = verification.get("family_scorecards") if isinstance(verification.get("family_scorecards"), list) else []

    changed_families = changed_config.get("changed_config_families") if isinstance(changed_config.get("changed_config_families"), list) else []
    changed_entries = changed_config.get("changed_config_entries") if isinstance(changed_config.get("changed_config_entries"), list) else []

    def metric_card(label: str, value: Any) -> str:
        return (
            '<div class="metric-card">'
            f"<span>{html.escape(label)}</span>"
            f"<strong>{html.escape(str(value))}</strong>"
            "</div>"
        )

    changed_family_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('label') or item.get('family') or 'other'))}</td>"
        f"<td>{html.escape(', '.join(item.get('config_keys') or []))}</td>"
        f"<td>{int(item.get('count') or 0)}</td>"
        "</tr>"
        for item in changed_families
    ) or "<tr><td colspan='3'>No changed config families captured for this approval.</td></tr>"
    changed_key_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('config_key') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('current_value') or ''))}</td>"
        f"<td>{html.escape(str(item.get('proposed_value') or ''))}</td>"
        f"<td>{html.escape(_family_label(str(item.get('family') or 'other')))}</td>"
        "</tr>"
        for item in changed_entries
    ) or "<tr><td colspan='4'>No changed config keys captured for this approval.</td></tr>"
    attribution_rows = "".join(
        f"<li>{html.escape(str(item))}</li>"
        for item in (attribution.get("notes") if isinstance(attribution.get("notes"), list) else [])
    ) or "<li>No attribution notes available.</li>"
    family_scorecard_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('label') or item.get('family') or 'other'))}</td>"
        f"<td>{int(item.get('rollout_count') or 0)}</td>"
        f"<td>{int(item.get('improved_count') or 0)}</td>"
        f"<td>{int(item.get('degraded_count') or 0)}</td>"
        f"<td>{float(item.get('avg_send_rate_delta') or 0.0)}%</td>"
        f"<td>{float(item.get('avg_post_win_rate') or 0.0)}%</td>"
        "</tr>"
        for item in family_scorecards[:6]
    ) or "<tr><td colspan='6'>No family scorecards available yet.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rollout Verification</title>
  <style>
    :root {{
      --bg: #081119; --panel: rgba(11,24,38,.9); --line: rgba(116,153,186,.16); --text: #edf5fb; --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--text); font-family: "Segoe UI", sans-serif; background: linear-gradient(180deg, #071018 0%, #09131c 100%); }}
    .shell {{ max-width: 1280px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; margin-top: 18px; }}
    .metric-grid {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:16px; }}
    .metric-card {{ background: rgba(18,34,52,.96); border: 1px solid var(--line); border-radius: 18px; padding: 16px; display:flex; flex-direction:column; gap:8px; }}
    .metric-card span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .metric-card strong {{ font-size: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    p, li {{ color: var(--muted); line-height: 1.5; }}
    ul {{ margin: 0; padding-left: 18px; }}
    @media (max-width: 900px) {{ .metric-grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Rollout Verification</h1>
      <p><strong>Approval:</strong> {html.escape(str(approval.get('approval_id') or 'unknown'))} &nbsp; <strong>Target:</strong> {html.escape(str(approval.get('target_name') or 'n/a'))} &nbsp; <strong>Service:</strong> {html.escape(str(approval.get('deployment_service') or 'n/a'))}</p>
      <p><strong>Status:</strong> {html.escape(str(verification.get('verification_status') or 'mixed'))}</p>
    </section>
    <section class="panel">
      <h2>Post-Rollout Deltas</h2>
      <div class="metric-grid">
        {metric_card("Send Rate Δ", f"{deltas.get('send_rate_delta', 0)}%")}
        {metric_card("Skip Pressure Δ", f"{deltas.get('skip_pressure_delta', 0)}%")}
        {metric_card("Block Pressure Δ", f"{deltas.get('block_pressure_delta', 0)}%")}
        {metric_card("Post Win Rate", f"{outcomes.get('win_rate', 0)}%")}
      </div>
    </section>
    <section class="panel">
      <h2>Comparison</h2>
      <div class="metric-grid">
        {metric_card("Pre Send Rate", f"{pre.get('send_rate', 0)}%")}
        {metric_card("Post Send Rate", f"{post.get('send_rate', 0)}%")}
        {metric_card("Pre Skip", f"{pre.get('skip_pressure', 0)}%")}
        {metric_card("Post Skip", f"{post.get('skip_pressure', 0)}%")}
      </div>
    </section>
    <section class="panel">
      <h2>Verification Reasons</h2>
      <ul>{"".join(f"<li>{html.escape(str(item))}</li>" for item in reasons) or "<li>No specific reason.</li>"}</ul>
    </section>
    <section class="panel">
      <h2>Changed Config</h2>
      <p>{html.escape(str(attribution.get('summary') or 'No attribution summary available.'))}</p>
      <table>
        <thead><tr><th>Family</th><th>Keys</th><th>Count</th></tr></thead>
        <tbody>{changed_family_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Changed Keys Detail</h2>
      <table>
        <thead><tr><th>Config Key</th><th>Current</th><th>Proposed</th><th>Family</th></tr></thead>
        <tbody>{changed_key_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Attribution Notes</h2>
      <ul>{attribution_rows}</ul>
    </section>
    <section class="panel">
      <h2>Historical Family Scorecards</h2>
      <table>
        <thead><tr><th>Family</th><th>Rollouts</th><th>Improved</th><th>Degraded</th><th>Avg Send Δ</th><th>Avg Post Win Rate</th></tr></thead>
        <tbody>{family_scorecard_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Runtime Drift</h2>
      <p>{html.escape(str((drift or {}).get('drift_count', 0)))} drift items against current runtime.</p>
    </section>
  </div>
</body>
</html>"""


def apply_rollout_verification(
    *,
    approval_id: str | None = None,
    target_name: str | None = None,
    deployment_service: str | None = None,
    baseline_hours: int = 24,
    post_hours: int = 24,
) -> dict[str, Any]:
    verification = get_rollout_verification(
        approval_id=approval_id,
        target_name=target_name,
        deployment_service=deployment_service,
        baseline_hours=baseline_hours,
        post_hours=post_hours,
    )
    approval = verification.get("approval") if isinstance(verification.get("approval"), dict) else None
    if not approval:
        raise KeyError("rollout_not_found")

    status_map = {
        "improved": "validated",
        "mixed": "review_needed",
        "degraded": "degraded",
        "insufficient_data": "pending_outcomes",
    }
    verification_status = status_map.get(str(verification.get("verification_status") or "mixed"), "review_needed")
    changed_config = verification.get("changed_config") if isinstance(verification.get("changed_config"), dict) else {}
    attribution = verification.get("attribution") if isinstance(verification.get("attribution"), dict) else {}
    changed_keys = changed_config.get("changed_config_keys") if isinstance(changed_config.get("changed_config_keys"), list) else []
    summary = (
        f"{verification_status}: send_rate_delta={verification['deltas']['send_rate_delta']} "
        f"skip_delta={verification['deltas']['skip_pressure_delta']} "
        f"block_delta={verification['deltas']['block_pressure_delta']} "
        f"post_win_rate={verification['post_outcomes']['win_rate']} "
        f"changed_keys={','.join(str(item) for item in changed_keys[:5]) or 'none'} "
        f"focus={str(attribution.get('summary') or '')}"
    )
    verification_ts = int(time.time())

    with sls._connect() as c:
        c.execute(
            """
            UPDATE tuning_approvals
            SET verification_status=?, verification_ts=?, verification_summary=?
            WHERE approval_id=?
            """,
            (verification_status, verification_ts, summary[:500], approval["approval_id"]),
        )

    if verification_status in {"degraded", "review_needed"}:
        emit_rollout_notification(
            event_type="rollout_verification",
            level="warning" if verification_status == "degraded" else "info",
            message=f"Rollout verification for {approval.get('target_name') or approval['approval_id']} returned {verification_status}.",
            target_name=str(approval.get("target_name") or ""),
            approval_id=str(approval["approval_id"]),
            deployment_service=str(approval.get("deployment_service") or ""),
            deployment_sha=str(approval.get("deployment_sha") or ""),
            payload={
                "verification_status": verification_status,
                "verification_summary": summary,
                "verification_reasons": verification.get("verification_reasons") or [],
                "changed_config_keys": changed_keys,
                "attribution": attribution,
            },
        )

    refreshed = get_latest_tuning_approval(
        approval_kind=str(approval.get("approval_kind") or "profile"),
        artifact_kind=str(approval.get("artifact_kind") or "env"),
        target_name=str(approval.get("target_name") or "") or None,
        rollout_status=str(approval.get("rollout_status") or "rolled_out"),
    )
    return {
        "approval": refreshed or approval,
        "verification": verification,
        "applied_status": verification_status,
        "applied_summary": summary,
    }


def apply_pending_rollout_verifications(
    *,
    baseline_hours: int = 24,
    post_hours: int = 24,
    limit: int = 20,
    force: bool = False,
) -> dict[str, Any]:
    now_ts = int(time.time())
    approvals = list_tuning_approvals(limit=max(1, limit * 5), rollout_status="rolled_out")
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for approval in approvals:
        rollout_ts = int(approval.get("rolled_out_ts") or approval.get("created_ts") or 0)
        verification_status = str(approval.get("verification_status") or "")
        verification_ts = int(approval.get("verification_ts") or 0)
        age_seconds = max(0, now_ts - rollout_ts)

        if not force and age_seconds < _rollout_verification_min_age_seconds():
            skipped.append(
                {
                    "approval_id": approval["approval_id"],
                    "reason": "too_fresh",
                    "age_seconds": age_seconds,
                }
            )
            continue
        if not force and verification_status in {"validated", "degraded", "review_needed"} and verification_ts >= rollout_ts:
            skipped.append(
                {
                    "approval_id": approval["approval_id"],
                    "reason": "already_verified",
                    "verification_status": verification_status,
                }
            )
            continue

        try:
            results.append(
                apply_rollout_verification(
                    approval_id=approval["approval_id"],
                    baseline_hours=baseline_hours,
                    post_hours=post_hours,
                )
            )
        except KeyError:
            skipped.append({"approval_id": approval["approval_id"], "reason": "approval_missing"})

        if len(results) >= max(1, limit):
            break

    return {
        "applied": results,
        "skipped": skipped,
        "applied_count": len(results),
        "skipped_count": len(skipped),
    }


async def rollout_verification_worker() -> None:
    while True:
        try:
            result = apply_pending_rollout_verifications(
                baseline_hours=24,
                post_hours=24,
                limit=10,
                force=False,
            )
            logger.info(
                "[rollout-verification] applied=%s skipped=%s",
                int(result.get("applied_count") or 0),
                int(result.get("skipped_count") or 0),
            )
        except Exception as exc:
            logger.exception("[rollout-verification] worker iteration failed: %s", exc)
        await asyncio.sleep(_rollout_verification_poll_seconds())


def get_operator_command_center(hours: int = 24) -> dict[str, Any]:
    lookback = max(1, int(hours))
    engine_health = sls.get_engine_health_digest(hours=lookback)
    diagnostics = sls.get_diagnostics_summary(hours=lookback)
    rollout_summary = get_tuning_rollout_summary()
    incidents = list_notification_incidents(limit=10, active_only=True)
    drift = {
        profile: get_config_drift_report(target_name=profile, rollout_status="rolled_out")
        for profile in ("strict", "balanced", "aggressive")
    }
    policy_profiles = sls.list_policy_profiles(limit=10)
    policy_rollouts = sls.list_policy_rollouts(limit=10, active_only=False)
    active_policy_rollouts = sls.list_policy_rollouts(limit=10, active_only=True)
    latest_policy_replay = sls.get_latest_policy_replay()
    policy_approvals = sls.list_policy_approvals(limit=10)
    policy_events = sls.list_policy_rollout_events(limit=10)
    policy_guardrails = sls.evaluate_policy_guardrails(
        hours=lookback,
        min_samples=3,
        max_negative_rate=60.0,
        auto_apply=False,
    )
    policy_automation_status = sls.get_policy_automation_status()
    resolved_candidate_policy = sls.resolve_live_policy("candidate")
    resolved_promoted_policy = sls.resolve_live_policy("promoted")

    recommended_actions: list[str] = []
    rollout_actions = rollout_summary.get("recommended_actions")
    if isinstance(rollout_actions, list):
        recommended_actions.extend(str(item) for item in rollout_actions if item)
    latest_rollouts = rollout_summary.get("latest_by_service") if isinstance(rollout_summary.get("latest_by_service"), dict) else {}
    verification_notes = []
    verification_cards: list[dict[str, Any]] = []
    verification_family_scorecards: list[dict[str, Any]] = []
    for service_name, item in latest_rollouts.items():
        verification_status = str(item.get("verification_status") or "")
        if verification_status:
            verification_notes.append(f"{service_name}: {verification_status}")
        changed_keys: list[str] = []
        changed_families: list[str] = []
        summary_text = str(item.get("verification_summary") or "")
        approval_id = str(item.get("approval_id") or "")
        fallback_changed_config = _summarize_changed_config(_changed_config_entries(item))
        changed_keys = [
            str(key)
            for key in (fallback_changed_config.get("changed_config_keys") if isinstance(fallback_changed_config.get("changed_config_keys"), list) else [])
            if key
        ][:5]
        changed_families = [
            str(family.get("label") or family.get("family") or "")
            for family in (fallback_changed_config.get("changed_config_families") if isinstance(fallback_changed_config.get("changed_config_families"), list) else [])
            if isinstance(family, dict)
        ][:3]
        if approval_id:
            try:
                verification = get_rollout_verification(
                    approval_id=approval_id,
                    baseline_hours=lookback,
                    post_hours=lookback,
                )
                changed_config = verification.get("changed_config") if isinstance(verification.get("changed_config"), dict) else {}
                attribution = verification.get("attribution") if isinstance(verification.get("attribution"), dict) else {}
                verification_family_scorecards = _merge_family_scorecards(
                    verification_family_scorecards,
                    verification.get("family_scorecards") if isinstance(verification.get("family_scorecards"), list) else [],
                )
                changed_keys = [
                    str(key)
                    for key in (changed_config.get("changed_config_keys") if isinstance(changed_config.get("changed_config_keys"), list) else [])
                    if key
                ][:5]
                changed_families = [
                    str(family.get("label") or family.get("family") or "")
                    for family in (changed_config.get("changed_config_families") if isinstance(changed_config.get("changed_config_families"), list) else [])
                    if isinstance(family, dict)
                ][:3]
                if attribution.get("summary"):
                    summary_text = str(attribution.get("summary"))
            except Exception as exc:
                logger.warning(
                    "[command-center] verification lookup failed for approval_id=%s: %s",
                    approval_id,
                    exc,
                )
                pass
        verification_cards.append(
            {
                "service": service_name,
                "target_name": str(item.get("target_name") or "n/a"),
                "verification_status": verification_status or "unverified",
                "verification_summary": summary_text,
                "changed_keys": changed_keys,
                "changed_families": changed_families,
                "deployment_sha": str(item.get("deployment_sha") or ""),
            }
        )

    rollout_lookbacks = [
        int(item.get("lookback_hours") or 0)
        for item in latest_rollouts.values()
        if isinstance(item, dict)
    ]
    proposal_payload = build_tuning_proposals(hours=max([lookback] + [value for value in rollout_lookbacks if value > 0]))
    verification_family_scorecards = _merge_family_scorecards(
        verification_family_scorecards,
        proposal_payload.get("historical_family_scorecards")
        if isinstance(proposal_payload.get("historical_family_scorecards"), list)
        else [],
    )

    incident_state_counts: dict[str, int] = {"open": 0, "acknowledged": 0, "snoozed": 0, "resolved": 0}
    for item in incidents:
        state = str(item.get("state") or "open")
        incident_state_counts[state] = incident_state_counts.get(state, 0) + 1

    status = str(engine_health.get("status") or "unknown")
    storage = engine_health.get("storage") if isinstance(engine_health.get("storage"), dict) else {}
    if status in {"cold", "quiet"}:
        recommended_actions.insert(0, f"Engine status is {status}. Check gate pressure and recent decision flow before changing thresholds.")
    elif status in {"gated", "blocked"}:
        recommended_actions.insert(0, f"Engine status is {status}. Review recent skip/block reasons before rolling out more aggressive profiles.")
    if (
        status == "cold"
        and int(storage.get("signal_count") or 0) == 0
        and int(storage.get("decision_count") or 0) == 0
    ):
        recommended_actions.insert(
            0,
            f"Learning DB is empty at {storage.get('db_path', 'unknown')}. Verify worker and engine share SIGNAL_ENGINE_DB_PATH or the same mounted disk path.",
        )

    unresolved_drift = [name for name, payload in drift.items() if int(payload.get("drift_count") or 0) > 0]
    if unresolved_drift:
        recommended_actions.insert(0, f"Runtime config drift exists for: {', '.join(unresolved_drift)}.")
    if verification_notes:
        recommended_actions.insert(0, f"Latest rollout verification: {', '.join(verification_notes)}.")

    return {
        "lookback_hours": lookback,
        "engine_health": engine_health,
        "diagnostics": {
            "counts_by_decision": diagnostics.get("counts_by_decision") or {},
            "top_skip_reasons": diagnostics.get("top_skip_reasons") or [],
            "threshold_guidance": diagnostics.get("threshold_guidance") or [],
            "reason_quality": diagnostics.get("reason_quality") or [],
        },
        "rollout_summary": rollout_summary,
        "drift": drift,
        "storage": storage,
        "notifications": incidents,
        "rollout_verification": verification_notes,
        "rollout_verification_cards": verification_cards,
        "rollout_verification_family_scorecards": verification_family_scorecards,
        "incident_state_counts": incident_state_counts,
        "policy_profiles": policy_profiles,
        "policy_rollouts": policy_rollouts,
        "active_policy_rollouts": active_policy_rollouts,
        "latest_policy_replay": latest_policy_replay,
        "policy_approvals": policy_approvals,
        "policy_events": policy_events,
        "policy_guardrails": policy_guardrails,
        "policy_automation": policy_automation_status,
        "resolved_policies": {
            "candidate": resolved_candidate_policy,
            "promoted": resolved_promoted_policy,
        },
        "recommended_actions": recommended_actions[:8],
    }


def render_operator_command_center_html(hours: int = 24) -> str:
    center = get_operator_command_center(hours=max(1, hours))
    engine_health = center.get("engine_health") if isinstance(center.get("engine_health"), dict) else {}
    diagnostics = center.get("diagnostics") if isinstance(center.get("diagnostics"), dict) else {}
    rollout_summary = center.get("rollout_summary") if isinstance(center.get("rollout_summary"), dict) else {}
    drift = center.get("drift") if isinstance(center.get("drift"), dict) else {}
    storage = center.get("storage") if isinstance(center.get("storage"), dict) else {}
    notifications = center.get("notifications") if isinstance(center.get("notifications"), list) else []
    verification_cards = center.get("rollout_verification_cards") if isinstance(center.get("rollout_verification_cards"), list) else []
    verification_family_scorecards = center.get("rollout_verification_family_scorecards") if isinstance(center.get("rollout_verification_family_scorecards"), list) else []
    incident_state_counts = center.get("incident_state_counts") if isinstance(center.get("incident_state_counts"), dict) else {}
    policy_profiles = center.get("policy_profiles") if isinstance(center.get("policy_profiles"), list) else []
    policy_rollouts = center.get("policy_rollouts") if isinstance(center.get("policy_rollouts"), list) else []
    policy_approvals = center.get("policy_approvals") if isinstance(center.get("policy_approvals"), list) else []
    policy_events = center.get("policy_events") if isinstance(center.get("policy_events"), list) else []
    policy_guardrails = center.get("policy_guardrails") if isinstance(center.get("policy_guardrails"), dict) else {}
    policy_automation = center.get("policy_automation") if isinstance(center.get("policy_automation"), dict) else {}
    latest_policy_replay = center.get("latest_policy_replay") if isinstance(center.get("latest_policy_replay"), dict) else {}
    resolved_policies = center.get("resolved_policies") if isinstance(center.get("resolved_policies"), dict) else {}
    recommended_actions = center.get("recommended_actions") if isinstance(center.get("recommended_actions"), list) else []

    def metric_card(label: str, value: Any) -> str:
        return (
            '<div class="metric-card">'
            f"<span>{html.escape(label)}</span>"
            f"<strong>{html.escape(str(value))}</strong>"
            "</div>"
        )

    top_skip_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{int(item.get('count') or 0)}</td>"
        "</tr>"
        for item in (diagnostics.get("top_skip_reasons") or [])[:8]
    ) or "<tr><td colspan='2'>No skip reasons in this window.</td></tr>"

    action_rows = "".join(f"<li>{html.escape(str(item))}</li>" for item in recommended_actions) or "<li>No immediate actions.</li>"

    notification_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('event_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('level') or 'info'))}</td>"
        f"<td>{html.escape(str(item.get('deployment_service') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('message') or ''))}</td>"
        "</tr>"
        for item in notifications[:8]
    ) or "<tr><td colspan='4'>No rollout notifications.</td></tr>"

    alignment_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('target_name') or 'unknown'))}</td>"
        f"<td>{'yes' if item.get('aligned') else 'no'}</td>"
        f"<td>{html.escape(str(item.get('worker_sha') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('engine_sha') or 'n/a'))}</td>"
        "</tr>"
        for item in (rollout_summary.get("worker_engine_alignment") or [])
    ) or "<tr><td colspan='4'>No worker/engine rollout alignment data yet.</td></tr>"

    drift_cards = "".join(
        '<div class="metric-card">'
        f"<span>{html.escape(name.title())} Drift</span>"
        f"<strong>{int(payload.get('drift_count') or 0)}</strong>"
        "</div>"
        for name, payload in drift.items()
    ) or '<div class="metric-card"><span>Drift</span><strong>0</strong></div>'

    incident_cards = "".join(
        '<div class="metric-card">'
        f"<span>{html.escape(state.title())} Incidents</span>"
        f"<strong>{int(count)}</strong>"
        "</div>"
        for state, count in incident_state_counts.items()
    ) or '<div class="metric-card"><span>Incidents</span><strong>0</strong></div>'

    verification_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('service') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('target_name') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('verification_status') or 'unverified'))}</td>"
        f"<td>{html.escape(str(item.get('deployment_sha') or 'n/a'))}</td>"
        f"<td>"
        f"{html.escape(str(item.get('verification_summary') or ''))}"
        f"{'<br><small>Families: ' + html.escape(', '.join(item.get('changed_families') or []) if item.get('changed_families') else 'none') + '</small>'}"
        f"{'<br><small>Keys: ' + html.escape(', '.join(item.get('changed_keys') or []) if item.get('changed_keys') else 'none') + '</small>'}"
        f"</td>"
        "</tr>"
        for item in verification_cards
    ) or "<tr><td colspan='5'>No rollout verification data yet.</td></tr>"
    verification_family_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('label') or item.get('family') or 'other'))}</td>"
        f"<td>{int(item.get('rollout_count') or 0)}</td>"
        f"<td>{int(item.get('improved_count') or 0)}</td>"
        f"<td>{int(item.get('degraded_count') or 0)}</td>"
        f"<td>{float(item.get('avg_send_rate_delta') or 0.0)}%</td>"
        f"<td>{float(item.get('avg_post_win_rate') or 0.0)}%</td>"
        "</tr>"
        for item in verification_family_scorecards[:6]
    ) or "<tr><td colspan='6'>No family scorecard data yet.</td></tr>"
    resolved_candidate = resolved_policies.get("candidate") if isinstance(resolved_policies.get("candidate"), dict) else {}
    resolved_promoted = resolved_policies.get("promoted") if isinstance(resolved_policies.get("promoted"), dict) else {}
    replay_summary = "No replay runs recorded yet."
    if latest_policy_replay:
        replay_summary = (
            f"{html.escape(str(latest_policy_replay.get('policy_name') or 'unknown'))}"
            f" @ {html.escape(str(latest_policy_replay.get('policy_version') or 'n/a'))}"
            f" changed {int(latest_policy_replay.get('changed_count') or 0)}"
            f" / {int(latest_policy_replay.get('trace_count') or 0)} traces"
            f" ({float(latest_policy_replay.get('change_rate') or 0.0)}%)."
        )
    policy_rollout_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('policy_name') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('policy_version') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('rollout_mode') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('rollout_status') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('stage_scope') or 'all'))}</td>"
        f"<td>{int(item.get('traffic_percent') or 0)}%</td>"
        "</tr>"
        for item in policy_rollouts[:8]
    ) or "<tr><td colspan='6'>No policy rollouts recorded.</td></tr>"
    policy_guardrail_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('policy_name') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('policy_version') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('stage_scope') or 'all'))}</td>"
        f"<td>{int(item.get('samples') or 0)}</td>"
        f"<td>{float(item.get('negative_rate') or 0.0)}%</td>"
        f"<td>{html.escape(str(item.get('recommended_action') or 'hold'))}</td>"
        "</tr>"
        for item in (policy_guardrails.get("evaluations") or [])[:8]
    ) or "<tr><td colspan='6'>No active canary guardrail evaluations.</td></tr>"
    policy_approval_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('policy_name') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('policy_version') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('approval_status') or 'draft'))}</td>"
        f"<td>{html.escape(str(item.get('source_type') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('approved_by') or 'n/a'))}</td>"
        "</tr>"
        for item in policy_approvals[:8]
    ) or "<tr><td colspan='5'>No policy approvals recorded.</td></tr>"
    policy_event_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('event_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('event_status') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('policy_name') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('policy_version') or 'n/a'))}</td>"
        "</tr>"
        for item in policy_events[:8]
    ) or "<tr><td colspan='4'>No policy events recorded.</td></tr>"
    policy_profile_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('policy_name') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('policy_version') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('created_by') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('description') or ''))}</td>"
        "</tr>"
        for item in policy_profiles[:8]
    ) or "<tr><td colspan='4'>No policy profiles recorded.</td></tr>"
    latest_automation_run = policy_automation.get("latest_run") if isinstance(policy_automation.get("latest_run"), dict) else {}
    automation_runs = policy_automation.get("recent_runs") if isinstance(policy_automation.get("recent_runs"), list) else []
    automation_guardrails = policy_automation.get("guardrails") if isinstance(policy_automation.get("guardrails"), dict) else {}
    automation_summary = "No automation runs recorded yet."
    if latest_automation_run:
        automation_summary = (
            f"{html.escape(str(latest_automation_run.get('status') or 'unknown'))}"
            f" run at {html.escape(str(latest_automation_run.get('created_ts') or 'n/a'))}"
            f" with {len(((latest_automation_run.get('approvals') or {}).get('created') or []))} approvals,"
            f" {len(((latest_automation_run.get('canaries') or {}).get('scheduled') or []))} canaries,"
            f" {len(((latest_automation_run.get('promotions') or {}).get('promoted') or []))} promotions."
        )
    automation_run_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('run_id') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('status') or 'unknown'))}</td>"
        f"<td>{len(((item.get('approvals') or {}).get('created') or []))}</td>"
        f"<td>{len(((item.get('canaries') or {}).get('scheduled') or []))}</td>"
        f"<td>{len(((item.get('promotions') or {}).get('promoted') or []))}</td>"
        f"<td>{len(((item.get('approvals') or {}).get('skipped') or [])) + len(((item.get('canaries') or {}).get('skipped') or [])) + len(((item.get('promotions') or {}).get('skipped') or []))}</td>"
        "</tr>"
        for item in automation_runs[:6]
    ) or "<tr><td colspan='6'>No automation history recorded.</td></tr>"
    budget_state = automation_guardrails.get("budgets") if isinstance(automation_guardrails.get("budgets"), dict) else {}
    cooldown_state = automation_guardrails.get("cooldowns") if isinstance(automation_guardrails.get("cooldowns"), dict) else {}

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Operator Command Center</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --panel-2: rgba(18, 34, 52, 0.96);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
      --accent: #d6b25e;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--text); font-family: "Segoe UI", sans-serif; background: linear-gradient(180deg, #071018 0%, #09131c 100%); }}
    .shell {{ max-width: 1440px; margin: 0 auto; padding: 24px 18px 36px; }}
    .hero, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; margin-top: 18px; }}
    .hero {{ margin-top: 0; }}
    .metric-grid {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:16px; }}
    .two-col {{ display:grid; grid-template-columns: 1.3fr 1fr; gap:18px; }}
    .metric-card {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 18px; padding: 16px; display:flex; flex-direction:column; gap:8px; }}
    .metric-card span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .metric-card strong {{ font-size: 24px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    h1 {{ margin: 0 0 8px; font-size: 36px; }}
    h2 {{ margin: 0 0 14px; font-size: 16px; letter-spacing: .10em; color: var(--muted); text-transform: uppercase; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    ul {{ margin: 0; padding-left: 18px; color: var(--text); }}
    .accent {{ color: var(--accent); }}
    @media (max-width: 1100px) {{
      .metric-grid, .two-col {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>Operator Command Center</h1>
      <p>Single-view ops surface for engine health, rollout state, config drift, and the latest policy notifications over the last {int(center.get("lookback_hours") or hours)} hours.</p>
    </section>
    <section class="panel">
      <h2>Health Snapshot</h2>
      <div class="metric-grid">
        {metric_card("Engine Status", engine_health.get("status", "unknown"))}
        {metric_card("Send Rate", f"{engine_health.get('send_rate', 0)}%")}
        {metric_card("Skip Pressure", f"{engine_health.get('skip_pressure', 0)}%")}
        {metric_card("Block Pressure", f"{engine_health.get('block_pressure', 0)}%")}
      </div>
      <p style="margin-top:14px;"><strong>DB:</strong> {html.escape(str(storage.get("db_path") or "unknown"))} &nbsp; <strong>Signals:</strong> {int(storage.get("signal_count") or 0)} &nbsp; <strong>Decisions:</strong> {int(storage.get("decision_count") or 0)}</p>
    </section>
    <section class="panel">
      <h2>Drift Snapshot</h2>
      <div class="metric-grid">
        {drift_cards}
      </div>
    </section>
    <section class="panel">
      <h2>Incident Snapshot</h2>
      <div class="metric-grid">
        {incident_cards}
      </div>
    </section>
    <section class="panel">
      <h2>Policy Ops</h2>
      <div class="metric-grid">
        {metric_card("Candidate Policy", f"{resolved_candidate.get('policy_name', 'default')} @ {resolved_candidate.get('policy_version', 'default')}")}
        {metric_card("Promoted Policy", f"{resolved_promoted.get('policy_name', 'default')} @ {resolved_promoted.get('policy_version', 'default')}")}
        {metric_card("Active Rollouts", len(center.get("active_policy_rollouts") or []))}
        {metric_card("Replay Changes", int(latest_policy_replay.get("changed_count") or 0))}
      </div>
      <p style="margin-top:14px;"><strong>Latest Replay:</strong> {replay_summary}</p>
      <p style="margin-top:10px;"><strong>Policy Automation:</strong> {automation_summary}</p>
      <p style="margin-top:10px;"><strong>Automation Budgets:</strong> approvals {int(budget_state.get('auto_approvals_used') or 0)}/{int(budget_state.get('auto_approvals_max') or 0)}, canaries {int(budget_state.get('canaries_used') or 0)}/{int(budget_state.get('canaries_max') or 0)}, promotions {int(budget_state.get('promotions_used') or 0)}/{int(budget_state.get('promotions_max') or 0)}. <strong>Cooldowns:</strong> approvals {int(cooldown_state.get('auto_approval_sec') or 0)}s, canaries {int(cooldown_state.get('canary_sec') or 0)}s, promotions {int(cooldown_state.get('promotion_sec') or 0)}s.</p>
    </section>
    <section class="panel">
      <h2>Automation Runs</h2>
      <table>
        <thead><tr><th>Run</th><th>Status</th><th>Approvals</th><th>Canaries</th><th>Promotions</th><th>Skipped</th></tr></thead>
        <tbody>{automation_run_rows}</tbody>
      </table>
    </section>
    <section class="two-col">
      <section class="panel">
        <h2>Recommended Actions</h2>
        <ul>{action_rows}</ul>
      </section>
      <section class="panel">
        <h2>Top Skip Reasons</h2>
        <table>
          <thead><tr><th>Reason</th><th>Count</th></tr></thead>
          <tbody>{top_skip_rows}</tbody>
        </table>
      </section>
    </section>
    <section class="two-col">
      <section class="panel">
        <h2>Rollout Notifications</h2>
        <table>
          <thead><tr><th>Event</th><th>Level</th><th>Service</th><th>Message</th></tr></thead>
          <tbody>{notification_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Worker / Engine Alignment</h2>
        <table>
          <thead><tr><th>Target</th><th>Aligned</th><th>Worker SHA</th><th>Engine SHA</th></tr></thead>
          <tbody>{alignment_rows}</tbody>
        </table>
      </section>
    </section>
    <section class="panel">
      <h2>Rollout Verification</h2>
      <table>
        <thead><tr><th>Service</th><th>Target</th><th>Verification</th><th>SHA</th><th>Summary</th></tr></thead>
        <tbody>{verification_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Policy Rollouts</h2>
      <table>
        <thead><tr><th>Policy</th><th>Version</th><th>Mode</th><th>Status</th><th>Stage</th><th>Traffic</th></tr></thead>
        <tbody>{policy_rollout_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Policy Guardrails</h2>
      <table>
        <thead><tr><th>Policy</th><th>Version</th><th>Stage</th><th>Samples</th><th>Negative Rate</th><th>Action</th></tr></thead>
        <tbody>{policy_guardrail_rows}</tbody>
      </table>
    </section>
    <section class="two-col">
      <section class="panel">
        <h2>Policy Approvals</h2>
        <table>
          <thead><tr><th>Policy</th><th>Version</th><th>Status</th><th>Source</th><th>Approved By</th></tr></thead>
          <tbody>{policy_approval_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Policy Events</h2>
        <table>
          <thead><tr><th>Event</th><th>Status</th><th>Policy</th><th>Version</th></tr></thead>
          <tbody>{policy_event_rows}</tbody>
        </table>
      </section>
    </section>
    <section class="panel">
      <h2>Policy Profiles</h2>
      <table>
        <thead><tr><th>Policy</th><th>Version</th><th>Created By</th><th>Description</th></tr></thead>
        <tbody>{policy_profile_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Verification Family Scorecards</h2>
      <table>
        <thead><tr><th>Family</th><th>Rollouts</th><th>Improved</th><th>Degraded</th><th>Avg Send Δ</th><th>Avg Post Win Rate</th></tr></thead>
        <tbody>{verification_family_rows}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def get_ops_digest(hours: int = 24) -> dict[str, Any]:
    center = get_operator_command_center(hours=max(1, hours))
    engine_health = center.get("engine_health") if isinstance(center.get("engine_health"), dict) else {}
    diagnostics = center.get("diagnostics") if isinstance(center.get("diagnostics"), dict) else {}
    rollout_summary = center.get("rollout_summary") if isinstance(center.get("rollout_summary"), dict) else {}
    drift = center.get("drift") if isinstance(center.get("drift"), dict) else {}
    notifications = center.get("notifications") if isinstance(center.get("notifications"), list) else []

    health_status = str(engine_health.get("status") or "unknown")
    counts = diagnostics.get("counts_by_decision") if isinstance(diagnostics.get("counts_by_decision"), dict) else {}
    sent_count = int(counts.get("sent") or 0)
    skip_count = int(counts.get("candidate_gate_skip") or 0)
    block_count = int(counts.get("promotion_block") or 0)
    skip_pressure = float(engine_health.get("skip_pressure") or 0.0)
    block_pressure = float(engine_health.get("block_pressure") or 0.0)

    drift_profiles = sorted(name for name, payload in drift.items() if int(payload.get("drift_count") or 0) > 0)
    blocking_notifications = [
        item
        for item in notifications
        if str(item.get("level") or "").lower() in {"warning", "error"}
        and not _is_ops_digest_event_type(item.get("event_type"))
    ]
    top_skip_reason = ""
    top_skip_reasons = diagnostics.get("top_skip_reasons")
    if isinstance(top_skip_reasons, list) and top_skip_reasons:
        top_skip_reason = str(top_skip_reasons[0].get("reason") or "")

    recommended_actions = center.get("recommended_actions") if isinstance(center.get("recommended_actions"), list) else []
    highlights: list[str] = [
        f"Engine status: {health_status}",
        f"Sent {sent_count} alerts over the last {int(center.get('lookback_hours') or hours)}h",
        f"Skipped {skip_count} candidates and blocked {block_count} promotions",
    ]
    if top_skip_reason:
        highlights.append(f"Top skip reason: {top_skip_reason}")
    if drift_profiles:
        highlights.append(f"Runtime drift detected for: {', '.join(drift_profiles)}")
    if blocking_notifications:
        highlights.append(f"Open rollout notifications: {len(blocking_notifications)}")

    severity = "info"
    attention_reasons: list[str] = []
    policy = _ops_digest_policy()
    incident_level = "normal"
    incident_reasons: list[str] = []
    if health_status in {"cold", "quiet", "blocked"}:
        severity = "warning"
        attention_reasons.append(f"engine_{health_status}")
    if drift_profiles:
        severity = "warning"
        attention_reasons.append("config_drift")
    if blocking_notifications:
        severity = "warning"
        attention_reasons.append("rollout_notifications")
    if sent_count == 0 and (skip_count > 0 or block_count > 0):
        severity = "warning"
        attention_reasons.append("no_sends_with_pressure")

    if health_status == "cold" and sent_count == 0 and skip_count == 0 and block_count == 0:
        severity = "error"
        attention_reasons.append("possible_stall")

    if health_status == "cold" and sent_count == 0 and skip_count == 0 and block_count == 0:
        incident_level = "critical"
        incident_reasons.append("possible_stall")
    elif len(drift_profiles) >= int(policy["critical_drift_profiles"]):
        incident_level = "incident"
        incident_reasons.append("multi_profile_drift")
    elif len(blocking_notifications) >= int(policy["incident_notification_count"]):
        incident_level = "incident"
        incident_reasons.append("ops_notifications_spike")
    elif sent_count == 0 and skip_count >= int(policy["incident_zero_send_min_skips"]):
        incident_level = "incident"
        incident_reasons.append("zero_sends_with_gate_pressure")
    elif skip_pressure >= float(policy["incident_skip_pressure"]):
        incident_level = "incident"
        incident_reasons.append("extreme_skip_pressure")
    elif block_pressure >= float(policy["incident_block_pressure"]):
        incident_level = "incident"
        incident_reasons.append("extreme_block_pressure")
    elif health_status in {"blocked", "cold"}:
        incident_level = "degraded"
        incident_reasons.append(f"engine_{health_status}")
    elif skip_pressure >= float(policy["degraded_skip_pressure"]):
        incident_level = "degraded"
        incident_reasons.append("high_skip_pressure")
    elif block_pressure >= float(policy["degraded_block_pressure"]):
        incident_level = "degraded"
        incident_reasons.append("high_block_pressure")
    elif health_status in {"quiet", "gated"} or drift_profiles or blocking_notifications:
        incident_level = "caution"
        if health_status in {"quiet", "gated"}:
            incident_reasons.append(f"engine_{health_status}")
        if drift_profiles:
            incident_reasons.append("config_drift")
        if blocking_notifications:
            incident_reasons.append("rollout_notifications")

    if incident_level in {"degraded", "incident"} and severity == "info":
        severity = "warning"
    if incident_level == "critical":
        severity = "error"

    summary_line = (
        f"{health_status.title()} engine over the last {int(center.get('lookback_hours') or hours)}h. "
        f"Sent {sent_count}, skipped {skip_count}, blocked {block_count}."
    )
    if drift_profiles:
        summary_line += f" Drift on {', '.join(drift_profiles)}."
    elif top_skip_reason:
        summary_line += f" Primary gate pressure: {top_skip_reason}."
    if incident_level != "normal":
        summary_line += f" Escalation: {incident_level}."

    return {
        "lookback_hours": int(center.get("lookback_hours") or hours),
        "severity": severity,
        "incident_level": incident_level,
        "incident_reasons": incident_reasons,
        "policy": policy,
        "needs_attention": bool(attention_reasons),
        "attention_reasons": attention_reasons,
        "summary": summary_line,
        "highlights": highlights[:6],
        "recommended_actions": recommended_actions[:5],
        "counts": {
            "sent": sent_count,
            "candidate_gate_skip": skip_count,
            "promotion_block": block_count,
        },
        "top_skip_reason": top_skip_reason or None,
        "drift_profiles": drift_profiles,
        "notification_count": len(blocking_notifications),
        "command_center": center,
        "rollout_alignment": rollout_summary.get("worker_engine_alignment") or [],
    }


def render_ops_digest_text(hours: int = 24) -> str:
    digest = get_ops_digest(hours=max(1, hours))
    lines = [
        "# Signal Engine Ops Digest",
        f"Severity: {digest['severity']}",
        f"Incident Level: {digest.get('incident_level', 'normal')}",
        f"Window: {digest['lookback_hours']}h",
        f"Summary: {digest['summary']}",
        "",
        "Highlights:",
    ]
    lines.extend(f"- {item}" for item in digest.get("highlights") or ["No highlights."])
    lines.append("")
    lines.append("Recommended Actions:")
    actions = digest.get("recommended_actions") or []
    lines.extend(f"- {item}" for item in actions if item)
    if not actions:
        lines.append("- No immediate actions.")
    return "\n".join(lines).strip()


def render_ops_digest_html(hours: int = 24) -> str:
    digest = get_ops_digest(hours=max(1, hours))
    highlights = digest.get("highlights") if isinstance(digest.get("highlights"), list) else []
    actions = digest.get("recommended_actions") if isinstance(digest.get("recommended_actions"), list) else []
    counts = digest.get("counts") if isinstance(digest.get("counts"), dict) else {}

    highlight_rows = "".join(f"<li>{html.escape(str(item))}</li>" for item in highlights) or "<li>No highlights.</li>"
    action_rows = "".join(f"<li>{html.escape(str(item))}</li>" for item in actions) or "<li>No immediate actions.</li>"
    count_cards = "".join(
        '<div class="metric-card">'
        f"<span>{html.escape(label.replace('_', ' ').title())}</span>"
        f"<strong>{int(value)}</strong>"
        "</div>"
        for label, value in counts.items()
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ops Digest</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--text); font-family: "Segoe UI", sans-serif; background: linear-gradient(180deg, #071018 0%, #09131c 100%); }}
    .shell {{ max-width: 1100px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; margin-top: 18px; }}
    .metric-grid {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:16px; }}
    .metric-card {{ background: rgba(18, 34, 52, 0.96); border: 1px solid var(--line); border-radius: 18px; padding: 16px; display:flex; flex-direction:column; gap:8px; }}
    .metric-card span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .metric-card strong {{ font-size: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    ul {{ margin: 0; padding-left: 18px; }}
    @media (max-width: 900px) {{ .metric-grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Ops Digest</h1>
      <p><strong>Severity:</strong> {html.escape(str(digest.get('severity') or 'info'))} &nbsp; <strong>Incident:</strong> {html.escape(str(digest.get('incident_level') or 'normal'))} &nbsp; <strong>Window:</strong> {int(digest.get('lookback_hours') or hours)}h</p>
      <p>{html.escape(str(digest.get('summary') or ''))}</p>
    </section>
    <section class="panel">
      <h2>Decision Counts</h2>
      <div class="metric-grid">{count_cards}</div>
    </section>
    <section class="panel">
      <h2>Highlights</h2>
      <ul>{highlight_rows}</ul>
    </section>
    <section class="panel">
      <h2>Recommended Actions</h2>
      <ul>{action_rows}</ul>
    </section>
  </div>
</body>
</html>"""


def dispatch_ops_digest(
    hours: int = 24,
    *,
    force: bool = False,
    digest_type: str = "ops_digest",
    summary_override: str | None = None,
) -> dict[str, Any]:
    digest = get_ops_digest(hours=max(1, hours))
    digest_kind = str(digest_type or "ops_digest").strip().lower()
    signature = _typed_ops_digest_signature(digest_kind, digest)
    latest = _latest_rollout_notification(digest_kind)
    policy = _ops_digest_policy()
    if not force and latest:
        latest_payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
        latest_signature = str(latest_payload.get("digest_signature") or "")
        age_seconds = max(0, int(time.time()) - int(latest.get("created_ts") or 0))
        cooldown_seconds = _ops_digest_cooldown_seconds()
        if digest_kind == "degraded_digest":
            cooldown_seconds = int(policy["degraded_reminder_sec"])
        elif digest_kind == "daily_summary":
            cooldown_seconds = int(policy["daily_summary_interval_sec"])
        if latest_signature == signature and age_seconds < cooldown_seconds:
            return {
                "dispatched": False,
                "reason": "cooldown_unchanged_digest",
                "digest_type": digest_kind,
                "cooldown_seconds": cooldown_seconds,
                "age_seconds": age_seconds,
                "latest_notification_id": latest.get("notification_id"),
                "digest": digest,
            }
    if not force and digest_kind == "ops_digest":
        return {
            "dispatched": False,
            "reason": "no_attention_needed",
            "digest_type": digest_kind,
            "digest": digest,
        }
    if not force and not bool(digest.get("needs_attention")):
        return {
            "dispatched": False,
            "reason": "no_attention_needed",
            "digest_type": digest_kind,
            "digest": digest,
        }

    notification = emit_rollout_notification(
        event_type=digest_kind,
        level=str(digest.get("severity") or "info"),
        message=str(summary_override or digest.get("summary") or "Signal Engine ops digest"),
        target_name="command-center",
        deployment_service=_default_deployment_metadata().get("deployment_service") or None,
        deployment_sha=_default_deployment_metadata().get("deployment_sha") or None,
        payload={
            "digest_type": digest_kind,
            "lookback_hours": digest.get("lookback_hours"),
            "needs_attention": digest.get("needs_attention"),
            "attention_reasons": digest.get("attention_reasons"),
            "incident_level": digest.get("incident_level"),
            "highlights": digest.get("highlights"),
            "recommended_actions": digest.get("recommended_actions"),
            "counts": digest.get("counts"),
            "digest_signature": signature,
        },
    )
    return {
        "dispatched": True,
        "digest_type": digest_kind,
        "notification": notification,
        "digest": digest,
    }


def dispatch_policy_tiered_ops_digests() -> dict[str, Any]:
    digest_hours = max(48, _ops_digest_default_hours())
    digest = get_ops_digest(hours=digest_hours)
    incident_level = str(digest.get("incident_level") or "normal")
    dispatched: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    if incident_level in {"incident", "critical"}:
        result = dispatch_ops_digest(
            hours=int(digest.get("lookback_hours") or digest_hours),
            force=False,
            digest_type="incident_digest",
            summary_override=f"[incident] {digest.get('summary')}",
        )
        (dispatched if result.get("dispatched") else skipped).append(result)
    elif incident_level == "degraded":
        result = dispatch_ops_digest(
            hours=int(digest.get("lookback_hours") or digest_hours),
            force=False,
            digest_type="degraded_digest",
            summary_override=f"[degraded] {digest.get('summary')}",
        )
        (dispatched if result.get("dispatched") else skipped).append(result)

    summary_result = dispatch_ops_digest(
        hours=_ops_daily_summary_hours(),
        force=False,
        digest_type="daily_summary",
        summary_override=f"[daily-summary] {get_ops_digest(hours=_ops_daily_summary_hours()).get('summary')}",
    )
    (dispatched if summary_result.get("dispatched") else skipped).append(summary_result)

    return {
        "incident_level": incident_level,
        "dispatched": dispatched,
        "skipped": skipped,
    }


async def ops_digest_worker() -> None:
    while True:
        try:
            result = dispatch_policy_tiered_ops_digests()
            logger.info(
                "[ops-digest] incident_level=%s dispatched=%s skipped=%s",
                result.get("incident_level") or "normal",
                len(result.get("dispatched") or []),
                len(result.get("skipped") or []),
            )
        except Exception as exc:
            logger.exception("[ops-digest] worker iteration failed: %s", exc)
        await asyncio.sleep(_ops_digest_poll_seconds())


def build_tuning_proposals(hours: int = 72) -> dict[str, Any]:
    summary = sls.get_diagnostics_summary(hours=max(1, hours))
    guidance = summary.get("threshold_guidance") if isinstance(summary.get("threshold_guidance"), list) else []
    family_scorecards = _rollout_family_scorecards(
        baseline_hours=max(1, hours),
        post_hours=max(1, hours),
        focus_families=[],
        limit=100,
    )
    proposals: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    current_map: dict[str, tuple[str, Any]] = {
        "attention<0.20": ("EARLY_ATTENTION_MIN", float(cfg.EARLY_ATTENTION_MIN)),
        "buyers_low": ("PROMOTION_MIN_ATTENTION", float(cfg.PROMOTION_MIN_ATTENTION)),
        "dex_gate:liq<12000.0": ("PROM_MIN_LIQ_USD", float(cfg.PROM_MIN_LIQ_USD)),
        "risk_high": ("PROMOTION_MAX_RISK", float(cfg.PROMOTION_MAX_RISK)),
        "age<30s": ("CAND_MIN_TOKEN_AGE_SEC", int(cfg.CAND_MIN_TOKEN_AGE_SEC)),
    }

    for item in guidance:
        reason = str(item.get("reason") or "")
        action = str(item.get("action") or "hold")
        confidence = str(item.get("confidence") or "low")
        sample_size = int(item.get("sample_size") or 0)
        if action not in {"relax_slightly", "tighten"}:
            deferred.append(
                {
                    "reason": reason,
                    "action": action,
                    "confidence": confidence,
                    "sample_size": sample_size,
                    "rationale": str(item.get("rationale") or ""),
                }
            )
            continue
        if reason not in current_map:
            deferred.append(
                {
                    "reason": reason,
                    "action": action,
                    "confidence": confidence,
                    "sample_size": sample_size,
                    "rationale": "No explicit config mapping exists for this blocker yet.",
                }
            )
            continue

        config_key, current_value = current_map[reason]
        proposed_value = current_value
        if config_key == "EARLY_ATTENTION_MIN":
            delta = 0.02
            proposed_value = round(max(0.05, current_value - delta), 2) if action == "relax_slightly" else round(min(0.5, current_value + delta), 2)
        elif config_key == "PROMOTION_MIN_ATTENTION":
            delta = 0.03
            proposed_value = round(max(0.25, current_value - delta), 2) if action == "relax_slightly" else round(min(0.9, current_value + delta), 2)
        elif config_key == "PROM_MIN_LIQ_USD":
            delta = 2000.0
            proposed_value = max(2000.0, current_value - delta) if action == "relax_slightly" else current_value + delta
        elif config_key == "PROMOTION_MAX_RISK":
            delta = 0.03
            proposed_value = round(min(0.9, current_value + delta), 2) if action == "relax_slightly" else round(max(0.2, current_value - delta), 2)
        elif config_key == "CAND_MIN_TOKEN_AGE_SEC":
            delta = 5
            proposed_value = max(5, current_value - delta) if action == "relax_slightly" else current_value + delta

        family = CONFIG_KEY_FAMILIES.get(config_key, "other")
        historical_support = _proposal_historical_support(family=family, family_scorecards=family_scorecards)
        if str(historical_support.get("support") or "unknown") == "unknown":
            historical_support = _fallback_proposal_historical_support(
                family=family,
                action=action,
                sample_size=sample_size,
                positive_rate=float(item.get("positive_rate") or 0.0),
                fail_rate=float(item.get("fail_rate") or 0.0),
            )
        proposal_priority, evidence_summary = _proposal_priority(
            action=action,
            confidence=confidence,
            sample_size=sample_size,
            historical_support=historical_support,
        )

        proposal = _proposal(
            reason=reason,
            action=action,
            config_key=config_key,
            current_value=current_value,
            proposed_value=proposed_value,
            confidence=confidence,
            sample_size=sample_size,
            rationale=str(item.get("rationale") or ""),
        )
        proposal["family"] = family
        proposal["historical_support"] = historical_support
        proposal["proposal_priority"] = proposal_priority
        proposal["evidence_summary"] = evidence_summary
        proposals.append(proposal)

    proposals.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(str(item.get("proposal_priority") or "low"), 3),
            {"tighten": 0, "relax_slightly": 1}.get(str(item["action"]), 2),
            {"supportive": 0, "neutral": 1, "unknown": 2, "caution": 3}.get(
                str((item.get("historical_support") if isinstance(item.get("historical_support"), dict) else {}).get("support") or "unknown"),
                4,
            ),
            {"high": 0, "medium": 1, "low": 2}.get(str(item["confidence"]), 3),
            -int(item["sample_size"]),
            str(item["config_key"]),
        )
    )

    preset_overrides = {
        "strict": {},
        "balanced": {},
        "aggressive": {},
    }
    for item in proposals:
        key = str(item["config_key"])
        proposed = item["proposed_value"]
        current = item["current_value"]
        action = str(item["action"])
        if action == "tighten":
            preset_overrides["strict"][key] = proposed
            preset_overrides["balanced"].setdefault(key, current)
        elif action == "relax_slightly":
            preset_overrides["aggressive"][key] = proposed
            preset_overrides["balanced"].setdefault(key, current)

    if proposals:
        family_scorecards = _merge_family_scorecards(
            family_scorecards,
            _fallback_family_scorecards_from_proposals(proposals),
        )

    return {
        "lookback_hours": hours,
        "generated_from": "diagnostics.threshold_guidance",
        "proposal_count": len(proposals),
        "proposals": proposals,
        "deferred": deferred[:10],
        "preset_overrides": preset_overrides,
        "historical_family_scorecards": family_scorecards[:10],
    }


def build_tuning_profiles(hours: int = 72) -> dict[str, Any]:
    proposal_payload = build_tuning_proposals(hours=max(1, hours))
    overrides = proposal_payload.get("preset_overrides") if isinstance(proposal_payload.get("preset_overrides"), dict) else {}
    baseline = _profile_baseline()
    profiles: dict[str, dict[str, Any]] = {}
    profile_diffs: dict[str, list[dict[str, Any]]] = {}

    for profile_name in ("strict", "balanced", "aggressive"):
        profile_values = dict(baseline)
        profile_values.update(overrides.get(profile_name, {}) if isinstance(overrides.get(profile_name), dict) else {})
        profiles[profile_name] = profile_values
        profile_diffs[profile_name] = [
            {
                "config_key": key,
                "current_value": baseline[key],
                "proposed_value": profile_values[key],
            }
            for key in PROFILE_CONFIG_KEYS
            if profile_values.get(key) != baseline.get(key)
        ]

    return {
        "lookback_hours": proposal_payload.get("lookback_hours", hours),
        "base_profile": "balanced",
        "profile_keys": list(PROFILE_CONFIG_KEYS),
        "profiles": profiles,
        "profile_diffs": profile_diffs,
        "profile_labels": dict(PROFILE_LABELS),
        "proposal_count": proposal_payload.get("proposal_count", 0),
    }


def render_tuning_env_snippet(hours: int = 72) -> str:
    payload = build_tuning_proposals(hours=max(1, hours))
    proposals = payload.get("proposals") if isinstance(payload.get("proposals"), list) else []
    lines = [
        "# Signal Engine tuning proposal export",
        f"# Generated from the last {int(payload.get('lookback_hours') or hours)} hours of diagnostics",
        "# Apply manually after review. Nothing here is auto-applied.",
    ]
    if not proposals:
        lines.append("# No concrete proposal overrides available.")
        return "\n".join(lines)

    for item in proposals:
        key = str(item.get("config_key") or "").strip()
        if not key:
            continue
        reason = str(item.get("reason") or "unknown")
        action = str(item.get("action") or "hold")
        confidence = str(item.get("confidence") or "low")
        priority = str(item.get("proposal_priority") or "low")
        historical = item.get("historical_support") if isinstance(item.get("historical_support"), dict) else {}
        sample_size = int(item.get("sample_size") or 0)
        lines.append(
            f"# {reason} | {action} | {confidence} confidence | priority {priority} | "
            f"historical {historical.get('support', 'unknown')} | sample {sample_size}"
        )
        lines.append(f"{key}={_format_env_value(item.get('proposed_value'))}")
    return "\n".join(lines)


def render_tuning_apply_diff(hours: int = 72) -> str:
    payload = build_tuning_proposals(hours=max(1, hours))
    proposals = payload.get("proposals") if isinstance(payload.get("proposals"), list) else []
    header = [
        "# Apply Manually",
        f"# Proposed config changes from the last {int(payload.get('lookback_hours') or hours)} hours",
    ]
    if not proposals:
        header.append("- No concrete proposal changes available.")
        return "\n".join(header)

    rows = []
    for item in proposals:
        rows.append(
            "- "
            f"{item.get('config_key')}: "
            f"{_format_diff_value(item.get('current_value'))} -> {_format_diff_value(item.get('proposed_value'))} "
            f"[{item.get('action')} | {item.get('confidence')} | {item.get('proposal_priority')} | "
            f"{(item.get('historical_support') if isinstance(item.get('historical_support'), dict) else {}).get('support', 'unknown')} | "
            f"{item.get('reason')}]"
        )
    return "\n".join(header + rows)


def render_profile_env_snippet(profile_name: str, hours: int = 72) -> str:
    payload = build_tuning_profiles(hours=max(1, hours))
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    profile = profiles.get(profile_name) if isinstance(profiles.get(profile_name), dict) else {}
    lines = [
        f"# Signal Engine {profile_name} profile",
        f"# {PROFILE_LABELS.get(profile_name, 'Generated config profile.')}",
        f"# Built from current config plus proposal overrides over the last {int(payload.get('lookback_hours') or hours)} hours",
    ]
    if not profile:
        lines.append("# Profile data unavailable.")
        return "\n".join(lines)
    for key in PROFILE_CONFIG_KEYS:
        if key in profile:
            lines.append(f"{key}={_format_env_value(profile[key])}")
    return "\n".join(lines)


def render_profile_apply_diff(profile_name: str, hours: int = 72) -> str:
    payload = build_tuning_profiles(hours=max(1, hours))
    diffs = payload.get("profile_diffs") if isinstance(payload.get("profile_diffs"), dict) else {}
    profile_diffs = diffs.get(profile_name) if isinstance(diffs.get(profile_name), list) else []
    header = [
        f"# {profile_name.title()} profile diff",
        "# Compared against the current balanced baseline",
    ]
    if not profile_diffs:
        header.append("- No config differences from baseline.")
        return "\n".join(header)
    rows = [
        f"- {item['config_key']}: {_format_diff_value(item['current_value'])} -> {_format_diff_value(item['proposed_value'])}"
        for item in profile_diffs
    ]
    return "\n".join(header + rows)


def render_tuning_proposals_html(hours: int = 72) -> str:
    payload = build_tuning_proposals(hours=max(1, hours))
    proposals = payload.get("proposals") if isinstance(payload.get("proposals"), list) else []
    deferred = payload.get("deferred") if isinstance(payload.get("deferred"), list) else []
    preset_overrides = payload.get("preset_overrides") if isinstance(payload.get("preset_overrides"), dict) else {}
    env_snippet = render_tuning_env_snippet(hours=max(1, hours))
    apply_diff = render_tuning_apply_diff(hours=max(1, hours))

    proposal_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('config_key') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('current_value') or ''))}</td>"
        f"<td>{html.escape(str(item.get('proposed_value') or ''))}</td>"
        f"<td>{html.escape(str(item.get('action') or 'hold'))}</td>"
        f"<td>{html.escape(str(item.get('confidence') or 'low'))}</td>"
        f"<td>{html.escape(str(item.get('proposal_priority') or 'low'))}</td>"
        f"<td>{html.escape(str((item.get('historical_support') if isinstance(item.get('historical_support'), dict) else {}).get('support') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('evidence_summary') or ''))}</td>"
        "</tr>"
        for item in proposals
    ) or "<tr><td colspan='9'>No concrete proposals available.</td></tr>"

    deferred_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('action') or 'hold'))}</td>"
        f"<td>{html.escape(str(item.get('confidence') or 'low'))}</td>"
        f"<td>{html.escape(str(item.get('rationale') or ''))}</td>"
        "</tr>"
        for item in deferred
    ) or "<tr><td colspan='4'>No deferred items.</td></tr>"

    preset_rows = ""
    for preset_name, overrides in preset_overrides.items():
        overrides = overrides if isinstance(overrides, dict) else {}
        body = "".join(
            f"<li><strong>{html.escape(str(key))}</strong>: {html.escape(str(value))}</li>"
            for key, value in sorted(overrides.items())
        ) or "<li>No overrides</li>"
        preset_rows += (
            '<div class="preset-card">'
            f"<h3>{html.escape(str(preset_name))}</h3>"
            f"<ul>{body}</ul>"
            "</div>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tuning Proposals</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --panel-2: rgba(18, 34, 52, 0.96);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      margin-top: 18px;
    }}
    .preset-grid {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:16px; }}
    .export-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:16px; }}
    .preset-card {{
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
    }}
    pre {{
      margin: 0;
      padding: 16px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(6, 16, 24, 0.92);
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      color: #d7e4ef;
      font-size: 13px;
      line-height: 1.5;
    }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 14px; font-size: 15px; letter-spacing: .12em; color: var(--muted); text-transform: uppercase; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    ul {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    @media (max-width: 1020px) {{
      .preset-grid {{ grid-template-columns: 1fr; }}
      .export-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Tuning Proposals</h1>
      <p>Review-only config suggestions generated from the last {int(payload.get("lookback_hours") or hours)} hours of diagnostics. Nothing here applies automatically.</p>
    </section>
    <section class="panel">
      <h2>Concrete Proposals</h2>
      <table>
        <thead><tr><th>Reason</th><th>Config</th><th>Current</th><th>Proposed</th><th>Action</th><th>Confidence</th><th>Priority</th><th>Historical</th><th>Evidence</th></tr></thead>
        <tbody>{proposal_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Preset Overrides</h2>
      <div class="preset-grid">{preset_rows}</div>
    </section>
    <section class="panel">
      <h2>Operational Exports</h2>
      <div class="export-grid">
        <div>
          <h3>.env Snippet</h3>
          <pre>{html.escape(env_snippet)}</pre>
        </div>
        <div>
          <h3>Apply Manually Diff</h3>
          <pre>{html.escape(apply_diff)}</pre>
        </div>
      </div>
    </section>
    <section class="panel">
      <h2>Deferred</h2>
      <table>
        <thead><tr><th>Reason</th><th>Action</th><th>Confidence</th><th>Why Deferred</th></tr></thead>
        <tbody>{deferred_rows}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def render_tuning_profiles_html(hours: int = 72) -> str:
    payload = build_tuning_profiles(hours=max(1, hours))
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    profile_diffs = payload.get("profile_diffs") if isinstance(payload.get("profile_diffs"), dict) else {}

    cards = ""
    for profile_name in ("strict", "balanced", "aggressive"):
        env_snippet = render_profile_env_snippet(profile_name, hours=max(1, hours))
        diff_text = render_profile_apply_diff(profile_name, hours=max(1, hours))
        diff_count = len(profile_diffs.get(profile_name) or [])
        cards += (
            '<section class="panel">'
            f"<h2>{html.escape(profile_name.title())}</h2>"
            f"<p>{html.escape(PROFILE_LABELS.get(profile_name, 'Generated config profile.'))}</p>"
            f"<p><strong>Changed keys vs balanced:</strong> {diff_count}</p>"
            '<div class="export-grid">'
            f"<div><h3>.env Profile</h3><pre>{html.escape(env_snippet)}</pre></div>"
            f"<div><h3>Manual Diff</h3><pre>{html.escape(diff_text)}</pre></div>"
            "</div>"
            "</section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tuning Profiles</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      margin-top: 18px;
    }}
    .export-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:16px; }}
    pre {{
      margin: 0;
      padding: 16px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(6, 16, 24, 0.92);
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      color: #d7e4ef;
      font-size: 13px;
      line-height: 1.5;
    }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 12px; font-size: 22px; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; }}
    p {{ margin: 0 0 12px; color: var(--muted); line-height: 1.5; }}
    @media (max-width: 1020px) {{
      .export-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Tuning Profiles</h1>
      <p>Complete env-ready profile bundles generated from the current baseline and proposal overrides from the last {int(payload.get("lookback_hours") or hours)} hours.</p>
    </section>
    {cards}
  </div>
</body>
</html>"""


def create_tuning_approval(
    *,
    approval_kind: str,
    artifact_kind: str,
    hours: int = 72,
    target_name: str | None = None,
    approved_by: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    lookback_hours = max(1, int(hours))
    kind = str(approval_kind or "").strip().lower()
    artifact = str(artifact_kind or "").strip().lower()
    target = str(target_name or "").strip().lower() or None

    if kind not in {"proposal", "profile"}:
        raise ValueError("invalid_approval_kind")
    if artifact not in {"env", "diff"}:
        raise ValueError("invalid_artifact_kind")
    if kind == "profile" and target not in {"strict", "balanced", "aggressive"}:
        raise ValueError("invalid_profile_target")

    if kind == "proposal":
        artifact_text = render_tuning_env_snippet(lookback_hours) if artifact == "env" else render_tuning_apply_diff(lookback_hours)
        payload = build_tuning_proposals(lookback_hours)
    else:
        artifact_text = (
            render_profile_env_snippet(target or "balanced", lookback_hours)
            if artifact == "env"
            else render_profile_apply_diff(target or "balanced", lookback_hours)
        )
        payload = build_tuning_profiles(lookback_hours)

    record = {
        "approval_id": f"tune-{uuid.uuid4().hex[:12]}",
        "created_ts": int(time.time()),
        "approved_by": (approved_by or "unknown").strip() or "unknown",
        "approval_kind": kind,
        "target_name": target,
        "artifact_kind": artifact,
        "lookback_hours": lookback_hours,
        "rollout_status": "pending",
        "rolled_out_ts": None,
        "deployment_service": "",
        "deployment_sha": "",
        "deployment_env": "",
        "verification_status": "",
        "verification_ts": None,
        "verification_summary": "",
        "notes": (notes or "").strip(),
        "artifact_text": artifact_text,
        "payload": payload,
    }
    with sls._connect() as c:
        c.execute(
            """
            INSERT INTO tuning_approvals (
                approval_id, created_ts, approved_by, approval_kind, target_name,
                artifact_kind, lookback_hours, rollout_status, rolled_out_ts,
                deployment_service, deployment_sha, deployment_env,
                verification_status, verification_ts, verification_summary,
                notes, artifact_text, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["approval_id"],
                record["created_ts"],
                record["approved_by"],
                record["approval_kind"],
                record["target_name"],
                record["artifact_kind"],
                record["lookback_hours"],
                record["rollout_status"],
                record["rolled_out_ts"],
                record["deployment_service"],
                record["deployment_sha"],
                record["deployment_env"],
                record["verification_status"],
                record["verification_ts"],
                record["verification_summary"],
                record["notes"],
                record["artifact_text"],
                json.dumps(record["payload"]),
            ),
        )
    return record


def list_tuning_approvals(
    limit: int = 20,
    *,
    approval_kind: str | None = None,
    artifact_kind: str | None = None,
    target_name: str | None = None,
    rollout_status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    with sls._connect() as c:
        rows = c.execute(
            """
            SELECT approval_id, created_ts, approved_by, approval_kind, target_name,
                   artifact_kind, lookback_hours, rollout_status, rolled_out_ts,
                   deployment_service, deployment_sha, deployment_env,
                   verification_status, verification_ts, verification_summary,
                   notes, artifact_text, payload_json
            FROM tuning_approvals
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    approvals: list[dict[str, Any]] = []
    for row in rows:
        approval = _normalize_approval(row)
        if _approval_matches(
            approval,
            approval_kind=approval_kind,
            artifact_kind=artifact_kind,
            target_name=target_name,
            rollout_status=rollout_status,
            query=query,
        ):
            approvals.append(approval)
        if len(approvals) >= max(1, int(limit)):
            break
    return approvals


def update_tuning_approval_status(
    approval_id: str,
    *,
    rollout_status: str,
    notes: str | None = None,
    deployment_service: str | None = None,
    deployment_sha: str | None = None,
    deployment_env: str | None = None,
    allow_misaligned: bool = False,
) -> dict[str, Any]:
    status = str(rollout_status or "").strip().lower()
    if status not in APPROVAL_STATUSES:
        raise ValueError("invalid_rollout_status")
    now_ts = int(time.time())
    note_text = (notes or "").strip()
    defaults = _default_deployment_metadata()
    service_value = (deployment_service or "").strip() or defaults["deployment_service"]
    sha_value = (deployment_sha or "").strip() or defaults["deployment_sha"]
    env_value = (deployment_env or "").strip() or defaults["deployment_env"]
    with sls._connect() as c:
        existing = c.execute(
            """
            SELECT approval_id, notes, rollout_status, deployment_service, deployment_sha, deployment_env,
                   target_name, approval_kind, artifact_text
            FROM tuning_approvals WHERE approval_id=?
            """,
            (approval_id,),
        ).fetchone()
        if not existing:
            raise KeyError("tuning_approval_not_found")
        current_status = str(existing["rollout_status"] or "pending")
        allowed_transitions = {
            "pending": {"approved", "rejected"},
            "approved": {"rolled_out", "rejected"},
            "rolled_out": set(),
            "rejected": set(),
        }
        if status != current_status and status not in allowed_transitions.get(current_status, set()):
            raise ValueError("invalid_rollout_transition")
        merged_notes = existing["notes"] or ""
        if note_text:
            merged_notes = f"{merged_notes}\n{note_text}".strip() if merged_notes else note_text
        effective_service = service_value or str(existing["deployment_service"] or "")
        effective_sha = sha_value or str(existing["deployment_sha"] or "")
        effective_env = env_value or str(existing["deployment_env"] or "")
        if status == "rolled_out" and (not effective_service or not effective_sha):
            raise ValueError("missing_deployment_metadata")
        target_name = str(existing["target_name"] or "")
        approval_kind = str(existing["approval_kind"] or "")
        if (
            status == "rolled_out"
            and approval_kind == "profile"
            and effective_service in ROLLOUT_COMPARISON_SERVICES
            and target_name.lower() in _required_aligned_profiles()
            and not allow_misaligned
        ):
            counterpart_service = "engine" if effective_service == "worker" else "worker"
            counterpart = _latest_rolled_out_profile_for_service(target_name, counterpart_service)
            if counterpart:
                candidate_alignment = {
                    "artifact_text": str(existing["artifact_text"] or ""),
                    "deployment_sha": effective_sha,
                }
                if not _approvals_aligned(counterpart, candidate_alignment):
                    emit_rollout_notification(
                        event_type="rollout_blocked",
                        level="warning",
                        message=f"Blocked {target_name} rollout on {effective_service}: required alignment with {counterpart_service} not satisfied.",
                        target_name=target_name,
                        approval_id=approval_id,
                        deployment_service=effective_service,
                        deployment_sha=effective_sha,
                        payload={
                            "counterpart_service": counterpart_service,
                            "counterpart_approval_id": counterpart.get("approval_id"),
                            "counterpart_sha": counterpart.get("deployment_sha"),
                        },
                    )
                    raise ValueError("alignment_guardrail_blocked")
        c.execute(
            """
            UPDATE tuning_approvals
            SET rollout_status=?, rolled_out_ts=?, deployment_service=?, deployment_sha=?, deployment_env=?, notes=?
            WHERE approval_id=?
            """,
            (
                status,
                now_ts if status == "rolled_out" else None,
                effective_service if status == "rolled_out" else "",
                effective_sha if status == "rolled_out" else "",
                effective_env if status == "rolled_out" else "",
                merged_notes,
                approval_id,
            ),
        )
    updated_item = None
    for item in list_tuning_approvals(limit=100):
        if item["approval_id"] == approval_id:
            updated_item = item
            break
    if updated_item is None:
        raise KeyError("tuning_approval_not_found")

    if status == "rolled_out":
        drift = get_config_drift_report(target_name=target_name, rollout_status="rolled_out") if target_name else None
        if target_name and drift is not None:
            emit_rollout_notification(
                event_type="drift_resolved",
                level="info",
                message=(
                    f"Runtime drift resolved for {target_name} on {effective_service}."
                    if int(drift.get('drift_count') or 0) == 0
                    else f"Runtime drift check recorded for {target_name} on {effective_service}; drift_count={int(drift.get('drift_count') or 0)}."
                ),
                target_name=target_name,
                approval_id=approval_id,
                deployment_service=effective_service,
                deployment_sha=effective_sha,
                payload={
                    "rollout_status": status,
                    "drift_count": int(drift.get("drift_count") or 0),
                },
            )
        counterpart_service = "engine" if effective_service == "worker" else "worker"
        counterpart = _latest_rolled_out_profile_for_service(target_name, counterpart_service) if target_name else None
        if target_name and counterpart and _approvals_aligned(updated_item, counterpart):
            emit_rollout_notification(
                event_type="required_profile_aligned",
                level="info",
                message=f"{target_name} is now aligned across {effective_service} and {counterpart_service}.",
                target_name=target_name,
                approval_id=approval_id,
                deployment_service=effective_service,
                deployment_sha=effective_sha,
                payload={
                    "counterpart_service": counterpart_service,
                    "counterpart_approval_id": counterpart.get("approval_id"),
                    "counterpart_sha": counterpart.get("deployment_sha"),
                },
            )

    return updated_item


def get_latest_tuning_approval(
    *,
    approval_kind: str,
    artifact_kind: str,
    target_name: str | None = None,
    rollout_status: str = "approved",
) -> dict[str, Any] | None:
    kind = str(approval_kind or "").strip().lower()
    artifact = str(artifact_kind or "").strip().lower()
    target = str(target_name or "").strip().lower() or None
    status = str(rollout_status or "approved").strip().lower()
    if kind not in {"proposal", "profile"}:
        raise ValueError("invalid_approval_kind")
    if artifact not in {"env", "diff"}:
        raise ValueError("invalid_artifact_kind")
    if status not in APPROVAL_STATUSES:
        raise ValueError("invalid_rollout_status")
    if kind == "profile" and target not in {"strict", "balanced", "aggressive"}:
        raise ValueError("invalid_profile_target")

    with sls._connect() as c:
        row = c.execute(
            """
            SELECT approval_id, created_ts, approved_by, approval_kind, target_name,
                   artifact_kind, lookback_hours, rollout_status, rolled_out_ts,
                   deployment_service, deployment_sha, deployment_env,
                   verification_status, verification_ts, verification_summary,
                   notes, artifact_text, payload_json
            FROM tuning_approvals
            WHERE approval_kind=? AND artifact_kind=? AND rollout_status=?
              AND (? IS NULL OR target_name=?)
            ORDER BY created_ts DESC
            LIMIT 1
            """,
            (kind, artifact, status, target, target),
        ).fetchone()
    if not row:
        return None
    return _normalize_approval(row)


def get_tuning_rollout_summary() -> dict[str, Any]:
    approvals = list_tuning_approvals(limit=200, rollout_status="rolled_out")
    latest_by_service: dict[str, dict[str, Any]] = {}
    latest_profiles_by_service: dict[str, dict[str, dict[str, Any]]] = {}
    for approval in approvals:
        service = str(approval.get("deployment_service") or "unknown")
        latest_by_service.setdefault(service, approval)
        if approval.get("approval_kind") == "profile" and approval.get("target_name"):
            latest_profiles_by_service.setdefault(service, {})
            latest_profiles_by_service[service].setdefault(str(approval["target_name"]), approval)

    worker_profiles = latest_profiles_by_service.get("worker", {})
    engine_profiles = latest_profiles_by_service.get("engine", {})
    compared_targets = sorted(set(worker_profiles.keys()) | set(engine_profiles.keys()))
    alignment = []
    for target in compared_targets:
        worker_item = worker_profiles.get(target)
        engine_item = engine_profiles.get(target)
        alignment.append(
            {
                "target_name": target,
                "worker_approval_id": worker_item.get("approval_id") if worker_item else None,
                "engine_approval_id": engine_item.get("approval_id") if engine_item else None,
                "worker_sha": worker_item.get("deployment_sha") if worker_item else None,
                "engine_sha": engine_item.get("deployment_sha") if engine_item else None,
                "aligned": bool(
                    _approvals_aligned(worker_item, engine_item)
                ),
            }
        )

    return {
        "defaults": _default_deployment_metadata(),
        "service_count": len(latest_by_service),
        "latest_by_service": latest_by_service,
        "latest_profiles_by_service": latest_profiles_by_service,
        "worker_engine_alignment": alignment,
        "required_alignment_profiles": sorted(_required_aligned_profiles()),
        "notifications": _rollout_notifications(alignment),
        "recommended_actions": _rollout_recommendations(alignment),
    }


def render_tuning_rollout_summary_html() -> str:
    summary = get_tuning_rollout_summary()
    latest_by_service = summary.get("latest_by_service") if isinstance(summary.get("latest_by_service"), dict) else {}
    alignment = summary.get("worker_engine_alignment") if isinstance(summary.get("worker_engine_alignment"), list) else []
    notifications = summary.get("notifications") if isinstance(summary.get("notifications"), list) else []
    recommendations = summary.get("recommended_actions") if isinstance(summary.get("recommended_actions"), list) else []

    service_cards = "".join(
        '<section class="panel">'
        f"<h2>{html.escape(service)}</h2>"
        f"<p><strong>Kind:</strong> {html.escape(str(item.get('approval_kind') or 'unknown'))} &nbsp; "
        f"<strong>Target:</strong> {html.escape(str(item.get('target_name') or 'n/a'))}</p>"
        f"<p><strong>SHA:</strong> {html.escape(str(item.get('deployment_sha') or 'n/a'))} &nbsp; "
        f"<strong>Env:</strong> {html.escape(str(item.get('deployment_env') or 'n/a'))}</p>"
        f"<p><strong>Approval:</strong> {html.escape(str(item.get('approval_id') or 'n/a'))}</p>"
        "</section>"
        for service, item in sorted(latest_by_service.items())
    ) or '<section class="panel"><h2>No Rollouts</h2><p>No rolled-out approvals recorded yet.</p></section>'

    alignment_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('target_name') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('worker_approval_id') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('engine_approval_id') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('worker_sha') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('engine_sha') or 'n/a'))}</td>"
        f"<td>{'yes' if item.get('aligned') else 'no'}</td>"
        "</tr>"
        for item in alignment
    ) or "<tr><td colspan='6'>No worker/engine comparison data yet.</td></tr>"

    notification_rows = "".join(
        f"<li><strong>{html.escape(str(item.get('level') or 'info').upper())}</strong>: {html.escape(str(item.get('message') or ''))}</li>"
        for item in notifications
    ) or "<li>No rollout notifications.</li>"

    recommendation_rows = "".join(
        f"<li>{html.escape(str(item))}</li>"
        for item in recommendations
    ) or "<li>No recommendations.</li>"

    defaults = summary.get("defaults") if isinstance(summary.get("defaults"), dict) else {}
    required = summary.get("required_alignment_profiles") if isinstance(summary.get("required_alignment_profiles"), list) else []
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tuning Rollout Summary</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--text); font-family: "Segoe UI", sans-serif; background: linear-gradient(180deg, #071018 0%, #09131c 100%); }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; margin-top: 18px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    p {{ margin: 0 0 12px; color: var(--muted); line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Tuning Rollout Summary</h1>
      <p><strong>Default Deployment Metadata:</strong> service={html.escape(defaults.get('deployment_service') or 'n/a')} sha={html.escape(defaults.get('deployment_sha') or 'n/a')} env={html.escape(defaults.get('deployment_env') or 'n/a')}</p>
      <p><strong>Required Alignment Profiles:</strong> {html.escape(', '.join(required) if required else 'none')}</p>
    </section>
    {service_cards}
    <section class="panel">
      <h2>Rollout Notifications</h2>
      <ul>{notification_rows}</ul>
    </section>
    <section class="panel">
      <h2>Recommended Actions</h2>
      <ul>{recommendation_rows}</ul>
    </section>
    <section class="panel">
      <h2>Worker / Engine Alignment</h2>
      <table>
        <thead><tr><th>Target</th><th>Worker Approval</th><th>Engine Approval</th><th>Worker SHA</th><th>Engine SHA</th><th>Aligned</th></tr></thead>
        <tbody>{alignment_rows}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def render_latest_tuning_bundle_artifact(
    *,
    artifact_kind: str = "env",
    rollout_status: str = "rolled_out",
) -> str:
    artifact = str(artifact_kind or "env").strip().lower()
    status = str(rollout_status or "rolled_out").strip().lower()
    if artifact not in {"env", "diff"}:
        raise ValueError("invalid_artifact_kind")
    if status not in APPROVAL_STATUSES:
        raise ValueError("invalid_rollout_status")

    sections = [
        "# Signal Engine latest approved bundle",
        f"# rollout_status={status}",
        f"# artifact_kind={artifact}",
    ]

    latest_items: list[tuple[str, dict[str, Any] | None]] = [
        ("proposal", get_latest_tuning_approval(approval_kind="proposal", artifact_kind=artifact, rollout_status=status)),
        ("strict", get_latest_tuning_approval(approval_kind="profile", target_name="strict", artifact_kind=artifact, rollout_status=status)),
        ("balanced", get_latest_tuning_approval(approval_kind="profile", target_name="balanced", artifact_kind=artifact, rollout_status=status)),
        ("aggressive", get_latest_tuning_approval(approval_kind="profile", target_name="aggressive", artifact_kind=artifact, rollout_status=status)),
    ]

    included = 0
    for name, approval in latest_items:
        if not approval:
            continue
        included += 1
        sections.append("")
        sections.append(f"# [{name}] approval_id={approval['approval_id']}")
        sections.append(str(approval.get("artifact_text") or "").strip())

    if included == 0:
        sections.append("# No matching approved artifacts found.")
    return "\n".join(sections).strip()


def get_config_drift_report(
    *,
    target_name: str,
    rollout_status: str = "rolled_out",
) -> dict[str, Any]:
    target = str(target_name or "").strip().lower()
    status = str(rollout_status or "rolled_out").strip().lower()
    if target not in {"strict", "balanced", "aggressive"}:
        raise ValueError("invalid_profile_target")
    if status not in APPROVAL_STATUSES:
        raise ValueError("invalid_rollout_status")

    latest = get_latest_tuning_approval(
        approval_kind="profile",
        target_name=target,
        artifact_kind="env",
        rollout_status=status,
    )
    runtime = _profile_baseline()
    if latest is None:
        return {
            "target_name": target,
            "rollout_status": status,
            "approval": None,
            "runtime": runtime,
            "drift": [],
            "drift_count": 0,
        }

    payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    expected = profiles.get(target) if isinstance(profiles.get(target), dict) else {}
    drift = []
    for key in PROFILE_CONFIG_KEYS:
        expected_value = expected.get(key)
        runtime_value = runtime.get(key)
        if expected_value != runtime_value:
            drift.append(
                {
                    "config_key": key,
                    "expected_value": expected_value,
                    "runtime_value": runtime_value,
                }
            )
    return {
        "target_name": target,
        "rollout_status": status,
        "approval": latest,
        "runtime": runtime,
        "drift": drift,
        "drift_count": len(drift),
    }


def render_tuning_approvals_html(
    limit: int = 20,
    *,
    approval_kind: str | None = None,
    artifact_kind: str | None = None,
    target_name: str | None = None,
    rollout_status: str | None = None,
    query: str | None = None,
) -> str:
    approvals = list_tuning_approvals(
        limit=max(1, limit),
        approval_kind=approval_kind,
        artifact_kind=artifact_kind,
        target_name=target_name,
        rollout_status=rollout_status,
        query=query,
    )
    drift_sections = "".join(
        '<section class="panel">'
        f"<h2>Config Drift / {html.escape(profile_name.title())}</h2>"
        + (
            "<p>No drift against the latest rolled-out profile.</p>"
            if drift_payload["drift_count"] == 0
            else "<pre>"
            + html.escape(
                "\n".join(
                    f"{item['config_key']}: expected {_format_diff_value(item['expected_value'])} | runtime {_format_diff_value(item['runtime_value'])}"
                    for item in drift_payload["drift"]
                )
            )
            + "</pre>"
        )
        + "</section>"
        for profile_name, drift_payload in (
            ("strict", get_config_drift_report(target_name="strict", rollout_status="rolled_out")),
            ("balanced", get_config_drift_report(target_name="balanced", rollout_status="rolled_out")),
            ("aggressive", get_config_drift_report(target_name="aggressive", rollout_status="rolled_out")),
        )
    )
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('approval_kind') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('target_name') or 'n/a'))}</td>"
        f"<td>{html.escape(str(item.get('artifact_kind') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('rollout_status') or 'approved'))}</td>"
        f"<td>{html.escape(str(item.get('verification_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('approved_by') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('deployment_service') or ''))}</td>"
        f"<td>{html.escape(str(item.get('deployment_sha') or ''))}</td>"
        f"<td>{int(item.get('lookback_hours') or 0)}</td>"
        f"<td>{html.escape(str(item.get('notes') or ''))}</td>"
        "</tr>"
        for item in approvals
    ) or "<tr><td colspan='10'>No tuning approvals recorded yet.</td></tr>"

    artifacts = "".join(
        '<section class="panel">'
        f"<h2>{html.escape(str(item.get('approval_kind') or 'unknown').title())} / {html.escape(str(item.get('target_name') or 'default'))}</h2>"
        f"<p><strong>Approved by:</strong> {html.escape(str(item.get('approved_by') or 'unknown'))} &nbsp; "
        f"<strong>Artifact:</strong> {html.escape(str(item.get('artifact_kind') or 'unknown'))} &nbsp; "
        f"<strong>Status:</strong> {html.escape(str(item.get('rollout_status') or 'pending'))} &nbsp; "
        f"<strong>Verification:</strong> {html.escape(str(item.get('verification_status') or 'n/a'))}</p>"
        f"<p><strong>Deploy:</strong> {html.escape(str(item.get('deployment_service') or 'n/a'))} / {html.escape(str(item.get('deployment_env') or 'n/a'))} / {html.escape(str(item.get('deployment_sha') or 'n/a'))}</p>"
        f"<p><strong>Verification Summary:</strong> {html.escape(str(item.get('verification_summary') or 'n/a'))}</p>"
        f"<pre>{html.escape(str(item.get('artifact_text') or ''))}</pre>"
        "</section>"
        for item in approvals[:5]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tuning Approvals</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--text); font-family: "Segoe UI", sans-serif; background: linear-gradient(180deg, #071018 0%, #09131c 100%); }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; margin-top: 18px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    pre {{ margin: 0; padding: 16px; border-radius: 16px; border: 1px solid var(--line); background: rgba(6, 16, 24, 0.92); white-space: pre-wrap; word-break: break-word; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    p {{ margin: 0 0 12px; color: var(--muted); line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Tuning Approvals</h1>
      <p>Persisted review decisions for proposal and profile artifacts. These records make rollout history explicit.</p>
      <p><strong>Filters:</strong> kind={html.escape(str(approval_kind or 'all'))}, artifact={html.escape(str(artifact_kind or 'all'))}, target={html.escape(str(target_name or 'all'))}, status={html.escape(str(rollout_status or 'all'))}, query={html.escape(str(query or 'none'))}</p>
    </section>
    <section class="panel">
      <h2>Approval Log</h2>
      <table>
        <thead><tr><th>Kind</th><th>Target</th><th>Artifact</th><th>Status</th><th>Verification</th><th>Approved By</th><th>Service</th><th>SHA</th><th>Hours</th><th>Notes</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    {drift_sections}
    {artifacts}
  </div>
</body>
</html>"""

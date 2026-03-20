from __future__ import annotations

import asyncio
import json

from app.services import signal_learning_service as sls
from app.services.tuning_service import (
    apply_rollout_verification,
    apply_pending_rollout_verifications,
    build_tuning_profiles,
    build_tuning_proposals,
    create_tuning_approval,
    dispatch_policy_tiered_ops_digests,
    dispatch_ops_digest,
    get_config_drift_report,
    get_latest_tuning_approval,
    get_ops_digest,
    get_operator_command_center,
    get_rollout_verification,
    list_notification_incidents,
    list_rollout_notifications,
    get_tuning_rollout_summary,
    list_tuning_approvals,
    render_ops_digest_html,
    render_ops_digest_text,
    render_notification_incidents_html,
    render_operator_command_center_html,
    ops_digest_worker,
    render_profile_apply_diff,
    render_profile_env_snippet,
    render_latest_tuning_bundle_artifact,
    render_rollout_verification_html,
    render_tuning_approvals_html,
    render_tuning_rollout_summary_html,
    render_tuning_apply_diff,
    render_tuning_env_snippet,
    render_tuning_profiles_html,
    render_tuning_proposals_html,
    rollout_verification_worker,
    update_incident_state,
    update_tuning_approval_status,
)


def test_build_tuning_proposals_maps_guidance_to_config_changes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SERVICE", "worker")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SHA", "auto123")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_ENV", "production")
    monkeypatch.setenv("SIGNAL_ENGINE_REQUIRED_ALIGNED_PROFILES", "strict")
    sls.init()

    base_ts = 1_773_620_000
    positive_ids = []
    negative_ids = []
    for offset in range(6):
        positive_ids.append(
            sls.record_signal_decision(
                token=f"token-positive-{offset}",
                event_type="candidate",
                stage="candidate",
                decision="candidate_gate_skip",
                reasons=["attention<0.20"],
                attention_score=0.19,
                risk_score=0.22,
                confidence_score=0.30,
                lifecycle="dex",
                ts_value=base_ts + offset,
                source="test",
            )
        )
        negative_ids.append(
            sls.record_signal_decision(
                token=f"token-negative-{offset}",
                event_type="candidate",
                stage="promoted",
                decision="promotion_block",
                reasons=["dex_gate:liq<12000.0"],
                attention_score=0.42,
                risk_score=0.28,
                confidence_score=0.48,
                lifecycle="dex",
                ts_value=base_ts + 100 + offset,
                source="test",
            )
        )

    with sls._connect() as c:
        for idx, signal_id in enumerate(positive_ids):
            c.execute(
                """
                INSERT INTO signal_snapshots (
                    signal_id, horizon_minutes, captured_ts, lifecycle, market_cap_usd, liquidity_usd,
                    volume_m5_usd, age_minutes, price_change_m5, price_change_h1, txns_m5_buys,
                    txns_m5_sells, market_cap_change_pct, liquidity_change_pct, volume_m5_change_pct,
                    outcome_label, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    60,
                    base_ts + 3600 + idx,
                    "dex",
                    18000,
                    4200,
                    3000,
                    64.0,
                    25.0,
                    60.0,
                    40,
                    18,
                    80.0,
                    3.0,
                    35.0,
                    "worked",
                    json.dumps({"outcome_label": "worked"}),
                ),
            )
        for idx, signal_id in enumerate(negative_ids):
            c.execute(
                """
                INSERT INTO signal_snapshots (
                    signal_id, horizon_minutes, captured_ts, lifecycle, market_cap_usd, liquidity_usd,
                    volume_m5_usd, age_minutes, price_change_m5, price_change_h1, txns_m5_buys,
                    txns_m5_sells, market_cap_change_pct, liquidity_change_pct, volume_m5_change_pct,
                    outcome_label, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    60,
                    base_ts + 7200 + idx,
                    "dex",
                    7000,
                    2600,
                    700,
                    65.0,
                    -25.0,
                    -45.0,
                    20,
                    70,
                    -41.0,
                    -42.0,
                    -70.0,
                    "failed",
                    json.dumps({"outcome_label": "failed"}),
                ),
            )

    proposals = build_tuning_proposals(hours=10_000)

    mapped = {item["reason"]: item for item in proposals["proposals"]}
    assert mapped["attention<0.20"]["config_key"] == "EARLY_ATTENTION_MIN"
    assert mapped["attention<0.20"]["action"] == "relax_slightly"
    assert mapped["attention<0.20"]["family"] == "candidate_attention"
    assert mapped["attention<0.20"]["historical_support"]["support"] == "supportive"
    assert mapped["attention<0.20"]["proposal_priority"] in {"high", "medium"}
    assert mapped["attention<0.20"]["evidence_summary"]
    assert mapped["dex_gate:liq<12000.0"]["config_key"] == "PROM_MIN_LIQ_USD"
    assert mapped["dex_gate:liq<12000.0"]["action"] == "tighten"
    assert mapped["dex_gate:liq<12000.0"]["family"] == "market_quality"
    assert mapped["dex_gate:liq<12000.0"]["historical_support"]["support"] == "caution"
    assert mapped["dex_gate:liq<12000.0"]["proposal_priority"] in {"high", "medium", "low"}
    assert "aggressive" in proposals["preset_overrides"]
    assert "strict" in proposals["preset_overrides"]
    assert proposals["historical_family_scorecards"]

    html = render_tuning_proposals_html(hours=10_000)
    assert "Tuning Proposals" in html
    assert "Concrete Proposals" in html
    assert ".env Snippet" in html
    assert "Apply Manually Diff" in html
    assert "Priority" in html
    assert "Historical" in html
    assert "Evidence" in html

    env_snippet = render_tuning_env_snippet(hours=10_000)
    assert "EARLY_ATTENTION_MIN=" in env_snippet
    assert "PROM_MIN_LIQ_USD=" in env_snippet
    assert "# attention<0.20 | relax_slightly" in env_snippet
    assert "priority" in env_snippet
    assert "historical supportive" in env_snippet

    apply_diff = render_tuning_apply_diff(hours=10_000)
    assert "EARLY_ATTENTION_MIN:" in apply_diff
    assert "PROM_MIN_LIQ_USD:" in apply_diff
    assert "->" in apply_diff
    assert "supportive" in apply_diff or "caution" in apply_diff

    profiles = build_tuning_profiles(hours=10_000)
    assert profiles["base_profile"] == "balanced"
    assert "strict" in profiles["profiles"]
    assert "aggressive" in profiles["profiles"]
    assert profiles["profiles"]["balanced"]["EARLY_ATTENTION_MIN"] == proposals["preset_overrides"]["balanced"].get(
        "EARLY_ATTENTION_MIN",
        profiles["profiles"]["balanced"]["EARLY_ATTENTION_MIN"],
    )
    assert profiles["profile_diffs"]["strict"]
    assert profiles["profile_diffs"]["aggressive"]

    strict_env = render_profile_env_snippet("strict", hours=10_000)
    assert "PROM_MIN_LIQ_USD=" in strict_env
    assert "Signal Engine strict profile" in strict_env

    aggressive_diff = render_profile_apply_diff("aggressive", hours=10_000)
    assert "EARLY_ATTENTION_MIN:" in aggressive_diff

    profiles_html = render_tuning_profiles_html(hours=10_000)
    assert "Tuning Profiles" in profiles_html
    assert "Balanced" in profiles_html
    assert "Aggressive" in profiles_html

    approval = create_tuning_approval(
        approval_kind="profile",
        artifact_kind="env",
        target_name="strict",
        hours=10_000,
        approved_by="ops",
        notes="Promote stricter profile for review",
    )
    assert approval["approval_kind"] == "profile"
    assert approval["artifact_kind"] == "env"
    assert "PROM_MIN_LIQ_USD=" in approval["artifact_text"]
    assert approval["rollout_status"] == "pending"

    approvals = list_tuning_approvals(limit=10)
    assert approvals
    assert approvals[0]["approved_by"] == "ops"
    assert approvals[0]["rollout_status"] == "pending"

    approved = update_tuning_approval_status(
        approval["approval_id"],
        rollout_status="approved",
        notes="Reviewed and approved",
    )
    assert approved["rollout_status"] == "approved"

    rolled_out = update_tuning_approval_status(
        approval["approval_id"],
        rollout_status="rolled_out",
        notes="Applied on Render",
    )
    assert rolled_out["rollout_status"] == "rolled_out"
    assert "Applied on Render" in rolled_out["notes"]
    assert rolled_out["deployment_service"] == "worker"
    assert rolled_out["deployment_sha"] == "auto123"

    latest = get_latest_tuning_approval(
        approval_kind="profile",
        target_name="strict",
        artifact_kind="env",
        rollout_status="rolled_out",
    )
    assert latest is not None
    assert latest["approval_id"] == approval["approval_id"]

    approvals_html = render_tuning_approvals_html(limit=10)
    assert "Tuning Approvals" in approvals_html
    assert "Approval Log" in approvals_html
    assert "rolled_out" in approvals_html

    filtered = list_tuning_approvals(limit=10, rollout_status="rolled_out", query="Render")
    assert filtered
    assert filtered[0]["approval_id"] == approval["approval_id"]

    notifications = list_rollout_notifications(limit=20)
    assert notifications
    assert any(item["event_type"] == "drift_resolved" for item in notifications)
    assert any(item["delivery_status"] == "disabled" for item in notifications)

    bundle = render_latest_tuning_bundle_artifact(artifact_kind="env", rollout_status="rolled_out")
    assert "[strict]" in bundle
    assert "PROM_MIN_LIQ_USD=" in bundle

    drift = get_config_drift_report(target_name="strict", rollout_status="rolled_out")
    assert drift["approval"] is not None
    assert isinstance(drift["drift"], list)

    summary = get_tuning_rollout_summary()
    assert summary["latest_by_service"]["worker"]["approval_id"] == approval["approval_id"]
    assert summary["defaults"]["deployment_sha"] == "auto123"
    assert summary["notifications"]
    assert any(item["code"] == "partial_rollout" for item in summary["notifications"])
    assert any("engine" in item.lower() for item in summary["recommended_actions"])

    summary_html = render_tuning_rollout_summary_html()
    assert "Tuning Rollout Summary" in summary_html
    assert "Worker / Engine Alignment" in summary_html
    assert "Recommended Actions" in summary_html

    command_center = get_operator_command_center(hours=24)
    assert command_center["engine_health"]["status"] in {"cold", "quiet", "processing", "gated", "blocked", "active"}
    assert "rollout_summary" in command_center
    assert "drift" in command_center
    assert "incident_state_counts" in command_center
    assert "rollout_verification_cards" in command_center
    assert "rollout_verification_family_scorecards" in command_center
    assert "recommended_actions" in command_center
    assert all("changed_keys" in item for item in command_center["rollout_verification_cards"])
    assert all("changed_families" in item for item in command_center["rollout_verification_cards"])
    assert any(item["family"] == "candidate_attention" for item in command_center["rollout_verification_family_scorecards"])

    command_center_html = render_operator_command_center_html(hours=24)
    assert "Operator Command Center" in command_center_html
    assert "Health Snapshot" in command_center_html
    assert "Incident Snapshot" in command_center_html
    assert "Rollout Verification" in command_center_html
    assert "Verification Family Scorecards" in command_center_html
    assert "Families:" in command_center_html or "Keys:" in command_center_html

    digest = get_ops_digest(hours=24)
    assert digest["severity"] in {"info", "warning", "error"}
    assert digest["incident_level"] in {"normal", "caution", "degraded", "incident", "critical"}
    assert "summary" in digest
    assert "highlights" in digest

    digest_text = render_ops_digest_text(hours=24)
    assert "Signal Engine Ops Digest" in digest_text
    assert "Incident Level:" in digest_text
    assert "Summary:" in digest_text

    digest_html = render_ops_digest_html(hours=24)
    assert "Ops Digest" in digest_html
    assert "Recommended Actions" in digest_html

    skipped_dispatch = dispatch_ops_digest(hours=24, force=False)
    assert skipped_dispatch["dispatched"] is False
    assert skipped_dispatch["reason"] == "no_attention_needed"

    forced_dispatch = dispatch_ops_digest(hours=24, force=True)
    assert forced_dispatch["dispatched"] is True
    assert forced_dispatch["notification"]["event_type"] == "ops_digest"

    cooldown_dispatch = dispatch_ops_digest(hours=24, force=False)
    assert cooldown_dispatch["dispatched"] is False
    assert cooldown_dispatch["reason"] == "cooldown_unchanged_digest"

    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SERVICE", "engine")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SHA", "diff999")
    second_approval = create_tuning_approval(
        approval_kind="profile",
        artifact_kind="env",
        target_name="strict",
        hours=10_000,
        approved_by="ops",
        notes="engine rollout candidate",
    )
    update_tuning_approval_status(
        second_approval["approval_id"],
        rollout_status="approved",
        notes="engine approved",
    )
    try:
        update_tuning_approval_status(
            second_approval["approval_id"],
            rollout_status="rolled_out",
            notes="should block on alignment guardrail",
        )
        assert False, "expected alignment_guardrail_blocked"
    except ValueError as exc:
        assert str(exc) == "alignment_guardrail_blocked"

    blocked_notifications = list_rollout_notifications(limit=20)
    assert any(item["event_type"] == "rollout_blocked" for item in blocked_notifications)

    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SHA", "auto123")
    aligned_approval = create_tuning_approval(
        approval_kind="profile",
        artifact_kind="env",
        target_name="strict",
        hours=10_000,
        approved_by="ops",
        notes="engine aligned rollout",
    )
    update_tuning_approval_status(
        aligned_approval["approval_id"],
        rollout_status="approved",
        notes="engine approved aligned",
    )
    final_rollout = update_tuning_approval_status(
        aligned_approval["approval_id"],
        rollout_status="rolled_out",
        notes="engine aligned rollout",
    )
    assert final_rollout["rollout_status"] == "rolled_out"
    aligned_notifications = list_rollout_notifications(limit=30)
    assert any(item["event_type"] == "required_profile_aligned" for item in aligned_notifications)
    active_notifications = list_rollout_notifications(limit=30, active_only=True)
    assert active_notifications


def test_ops_digest_worker_runs_single_iteration(monkeypatch):
    calls: list[str] = []

    def fake_dispatch_policy_tiered_ops_digests():
        calls.append("tick")
        return {
            "incident_level": "normal",
            "dispatched": [],
            "skipped": [],
        }

    async def fake_sleep(_: int):
        raise RuntimeError("stop-loop")

    monkeypatch.setenv("SIGNAL_ENGINE_OPS_DIGEST_HOURS", "12")
    monkeypatch.setenv("SIGNAL_ENGINE_OPS_DIGEST_POLL_SEC", "120")
    monkeypatch.setattr("app.services.tuning_service.dispatch_policy_tiered_ops_digests", fake_dispatch_policy_tiered_ops_digests)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(ops_digest_worker())
        assert False, "expected stop-loop"
    except RuntimeError as exc:
        assert str(exc) == "stop-loop"

    assert calls == ["tick"]


def test_ops_digest_escalates_to_incident_on_heavy_gate_pressure(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    base_ts = 1_773_620_000
    for offset in range(12):
        sls.record_signal_decision(
            token=f"token-{offset}",
            event_type="candidate",
            stage="candidate",
            decision="candidate_gate_skip",
            reasons=["attention<0.20"],
            attention_score=0.08,
            risk_score=0.35,
            confidence_score=0.18,
            lifecycle="dex",
            ts_value=base_ts + offset,
            source="test",
        )

    monkeypatch.setenv("SIGNAL_ENGINE_OPS_INCIDENT_ZERO_SEND_MIN_SKIPS", "10")
    digest = get_ops_digest(hours=10000)

    assert digest["incident_level"] == "incident"
    assert "zero_sends_with_gate_pressure" in digest["incident_reasons"]
    assert digest["severity"] in {"warning", "error"}


def test_rollout_notification_ack_and_snooze_states(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    from app.services.tuning_service import emit_rollout_notification, update_rollout_notification_state

    notification = emit_rollout_notification(
        event_type="rollout_blocked",
        level="warning",
        message="Blocked rollout for strict profile",
        target_name="strict",
    )

    active_before = list_rollout_notifications(limit=10, active_only=True)
    assert any(item["notification_id"] == notification["notification_id"] for item in active_before)

    acked = update_rollout_notification_state(
        notification["notification_id"],
        acknowledged=True,
        acknowledged_by="ops-user",
    )
    assert acked["acknowledged_by"] == "ops-user"
    assert acked["active"] is False

    active_after_ack = list_rollout_notifications(limit=10, active_only=True)
    assert all(item["notification_id"] != notification["notification_id"] for item in active_after_ack)

    unacked = update_rollout_notification_state(
        notification["notification_id"],
        acknowledged=False,
    )
    assert unacked["acknowledged_ts"] is None
    assert unacked["active"] is True

    snoozed = update_rollout_notification_state(
        notification["notification_id"],
        snooze_minutes=30,
    )
    assert snoozed["snoozed_until_ts"] is not None
    assert snoozed["active"] is False

    unsnoozed = update_rollout_notification_state(
        notification["notification_id"],
        unsnooze=True,
    )
    assert unsnoozed["snoozed_until_ts"] is None
    assert unsnoozed["active"] is True


def test_notification_incidents_cluster_repeated_events(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    from app.services.tuning_service import emit_rollout_notification

    emit_rollout_notification(
        event_type="rollout_blocked",
        level="warning",
        message="Blocked rollout for strict profile",
        target_name="strict",
        deployment_service="worker",
    )
    emit_rollout_notification(
        event_type="rollout_blocked",
        level="warning",
        message="Blocked rollout again for strict profile",
        target_name="strict",
        deployment_service="worker",
    )
    emit_rollout_notification(
        event_type="drift_resolved",
        level="info",
        message="Drift resolved for balanced",
        target_name="balanced",
        deployment_service="engine",
    )

    incidents = list_notification_incidents(limit=10)
    blocked_cluster = next(item for item in incidents if item["event_type"] == "rollout_blocked")
    assert blocked_cluster["count"] == 2
    assert blocked_cluster["target_name"] == "strict"
    assert blocked_cluster["deployment_service"] == "worker"

    incidents_html = render_notification_incidents_html(limit=10)
    assert "Notification Incidents" in incidents_html
    assert "Blocked rollout again for strict profile" in incidents_html


def test_incident_resolution_tracking(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    from app.services.tuning_service import emit_rollout_notification

    emit_rollout_notification(
        event_type="rollout_blocked",
        level="warning",
        message="Blocked rollout for strict profile",
        target_name="strict",
        deployment_service="worker",
    )
    emit_rollout_notification(
        event_type="rollout_blocked",
        level="warning",
        message="Blocked rollout again for strict profile",
        target_name="strict",
        deployment_service="worker",
    )

    acknowledged_incident = update_incident_state(
        event_type="rollout_blocked",
        target_name="strict",
        deployment_service="worker",
        acknowledged=True,
        acknowledged_by="ops-user",
    )
    assert acknowledged_incident["state"] == "acknowledged"
    assert acknowledged_incident["time_to_first_ack_seconds"] is not None

    resolved_incident = update_incident_state(
        event_type="rollout_blocked",
        target_name="strict",
        deployment_service="worker",
        resolved=True,
        resolved_by="ops-user",
        resolution_note="Resolved after rollout rollback",
    )
    assert resolved_incident["state"] == "resolved"
    assert resolved_incident["time_to_resolve_seconds"] is not None
    assert resolved_incident["resolution_note"] == "Resolved after rollout rollback"


def test_policy_tiered_ops_digests_emit_incident_and_daily_summary(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    base_ts = 1_773_620_000
    for offset in range(12):
        sls.record_signal_decision(
            token=f"token-{offset}",
            event_type="candidate",
            stage="candidate",
            decision="candidate_gate_skip",
            reasons=["attention<0.20"],
            attention_score=0.08,
            risk_score=0.35,
            confidence_score=0.18,
            lifecycle="dex",
            ts_value=base_ts + offset,
            source="test",
        )

    monkeypatch.setenv("SIGNAL_ENGINE_OPS_INCIDENT_ZERO_SEND_MIN_SKIPS", "10")
    result = dispatch_policy_tiered_ops_digests()

    dispatched_types = {item["digest_type"] for item in result["dispatched"]}
    assert result["incident_level"] == "critical"
    assert "incident_digest" in dispatched_types
    assert "daily_summary" in dispatched_types


def test_rollout_verification_compares_pre_and_post_windows(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SERVICE", "worker")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SHA", "verify123")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_ENV", "production")
    sls.init()

    guidance_base_ts = 1_773_620_000
    guidance_ids = []
    for offset in range(6):
        guidance_ids.append(
            sls.record_signal_decision(
                token=f"guide-{offset}",
                event_type="candidate",
                stage="candidate",
                decision="candidate_gate_skip",
                reasons=["attention<0.20"],
                attention_score=0.19,
                risk_score=0.22,
                confidence_score=0.30,
                lifecycle="dex",
                ts_value=guidance_base_ts + offset,
                source="test",
            )
        )
    with sls._connect() as c:
        for idx, signal_id in enumerate(guidance_ids):
            c.execute(
                """
                INSERT INTO signal_snapshots (
                    signal_id, horizon_minutes, captured_ts, lifecycle, market_cap_usd, liquidity_usd,
                    volume_m5_usd, age_minutes, price_change_m5, price_change_h1, txns_m5_buys,
                    txns_m5_sells, market_cap_change_pct, liquidity_change_pct, volume_m5_change_pct,
                    outcome_label, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    60,
                    guidance_base_ts + 3600 + idx,
                    "dex",
                    18000,
                    4200,
                    3000,
                    64.0,
                    25.0,
                    60.0,
                    40,
                    18,
                    80.0,
                    3.0,
                    35.0,
                    "worked",
                    json.dumps({"outcome_label": "worked"}),
                ),
            )

    approval = create_tuning_approval(
        approval_kind="proposal",
        artifact_kind="env",
        hours=48,
        approved_by="ops",
        notes="verification candidate",
    )
    update_tuning_approval_status(approval["approval_id"], rollout_status="approved", notes="approved")
    rolled_out = update_tuning_approval_status(approval["approval_id"], rollout_status="rolled_out", notes="rolled out")
    rollout_ts = int(rolled_out["rolled_out_ts"] or rolled_out["created_ts"])

    for offset in range(8):
        sls.record_signal_decision(
            token=f"pre-{offset}",
            event_type="candidate",
            stage="candidate",
            decision="candidate_gate_skip",
            reasons=["attention<0.20"],
            attention_score=0.12,
            risk_score=0.30,
            confidence_score=0.18,
            lifecycle="dex",
            ts_value=rollout_ts - 1800 - offset,
            source="test",
        )
    post_signal_ids = []
    for offset in range(6):
        signal_id = sls.record_signal_decision(
            token=f"post-{offset}",
            event_type="promoted",
            stage="promoted",
            decision="alert_sent",
            reasons=["passed"],
            attention_score=0.52,
            risk_score=0.22,
            confidence_score=0.58,
            lifecycle="dex",
            ts_value=rollout_ts + 600 + offset,
            source="test",
        )
        post_signal_ids.append(signal_id)

    with sls._connect() as c:
        for idx, signal_id in enumerate(post_signal_ids[:4]):
            c.execute(
                """
                INSERT INTO signal_snapshots (
                    signal_id, horizon_minutes, captured_ts, lifecycle, market_cap_usd, liquidity_usd,
                    volume_m5_usd, age_minutes, price_change_m5, price_change_h1, txns_m5_buys,
                    txns_m5_sells, market_cap_change_pct, liquidity_change_pct, volume_m5_change_pct,
                    outcome_label, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    60,
                    rollout_ts + 3600 + idx,
                    "dex",
                    22000,
                    5200,
                    3300,
                    80.0,
                    16.0,
                    35.0,
                    40,
                    20,
                    55.0,
                    8.0,
                    21.0,
                    "worked",
                    json.dumps({"outcome_label": "worked"}),
                ),
            )

    verification = get_rollout_verification(approval_id=approval["approval_id"], baseline_hours=1, post_hours=2)
    assert verification["approval"]["approval_id"] == approval["approval_id"]
    assert verification["pre_metrics"]["skipped"] >= 8
    assert verification["post_metrics"]["sent"] >= 6
    assert verification["post_outcomes"]["positive"] >= 4
    assert verification["verification_status"] in {"improved", "mixed"}
    assert "changed_config" in verification
    assert isinstance(verification["changed_config"]["changed_config_keys"], list)
    assert verification["attribution"]["summary"]
    assert isinstance(verification["family_scorecards"], list)

    verification_html = render_rollout_verification_html(approval_id=approval["approval_id"], baseline_hours=1, post_hours=2)
    assert "Rollout Verification" in verification_html
    assert "Post-Rollout Deltas" in verification_html
    assert "Changed Config" in verification_html
    assert "Historical Family Scorecards" in verification_html

    applied = apply_rollout_verification(approval_id=approval["approval_id"], baseline_hours=1, post_hours=2)
    assert applied["approval"]["approval_id"] == approval["approval_id"]
    assert applied["approval"]["verification_status"] in {"validated", "review_needed", "degraded", "pending_outcomes"}
    assert applied["approval"]["verification_summary"]
    assert "changed_keys=none" in applied["approval"]["verification_summary"]

    batch = apply_pending_rollout_verifications(baseline_hours=1, post_hours=2, limit=10, force=True)
    assert batch["applied_count"] >= 1


def test_rollout_verification_worker_runs_single_iteration(monkeypatch):
    calls: list[str] = []

    def fake_apply_pending_rollout_verifications(*, baseline_hours: int = 24, post_hours: int = 24, limit: int = 20, force: bool = False):
        calls.append(f"{baseline_hours}:{post_hours}:{limit}:{force}")
        return {"applied_count": 0, "skipped_count": 0, "applied": [], "skipped": []}

    async def fake_sleep(_: int):
        raise RuntimeError("stop-loop")

    monkeypatch.setenv("SIGNAL_ENGINE_ROLLOUT_VERIFY_POLL_SEC", "120")
    monkeypatch.setattr("app.services.tuning_service.apply_pending_rollout_verifications", fake_apply_pending_rollout_verifications)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(rollout_verification_worker())
        assert False, "expected stop-loop"
    except RuntimeError as exc:
        assert str(exc) == "stop-loop"

    assert calls == ["24:24:10:False"]


def test_command_center_tolerates_malformed_approval_payload(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

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
                "bad-approval-1",
                1_773_620_000,
                "ops",
                "profile",
                "strict",
                "env",
                24,
                "rolled_out",
                1_773_620_100,
                "worker",
                "sha-bad",
                "production",
                "",
                None,
                "",
                "bad payload row",
                "PROM_MIN_LIQ_USD=10000",
                "{not-json",
            ),
        )

    center = get_operator_command_center(hours=24)
    assert "rollout_verification_cards" in center

    html = render_operator_command_center_html(hours=24)
    assert "Operator Command Center" in html


def test_command_center_tolerates_malformed_notification_payload(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

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
                "bad-note-1",
                1_773_620_200,
                "rollout_blocked",
                "warning",
                "strict",
                "approval-x",
                "worker",
                "sha-x",
                "Malformed notification payload row",
                "{not-json",
                "disabled",
                None,
                "",
                None,
                "",
                None,
                None,
                "",
                "",
            ),
        )

    center = get_operator_command_center(hours=24)
    assert "notifications" in center

    html = render_operator_command_center_html(hours=24)
    assert "Operator Command Center" in html

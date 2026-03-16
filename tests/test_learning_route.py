from __future__ import annotations

from fastapi.testclient import TestClient

from app import main
from app.services import signal_learning_service as sls


def test_learning_report_route_returns_latest(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    report = sls.generate_daily_learning_report("2026-03-14")
    client = TestClient(main.app)
    response = client.get("/learning/report/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_date"] == report["report_date"]


def test_learning_report_latest_dashboard_returns_html(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.generate_daily_learning_report("2026-03-14")

    client = TestClient(main.app)
    response = client.get("/learning/report/latest/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Daily Learning Report" in response.text


def test_learning_report_latest_digest_returns_json_and_html(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.generate_daily_learning_report("2026-03-14")

    client = TestClient(main.app)

    json_response = client.get("/learning/report/latest/digest")
    assert json_response.status_code == 200
    payload = json_response.json()
    assert "highlights" in payload

    html_response = client.get("/learning/report/latest/digest/dashboard")
    assert html_response.status_code == 200
    assert "Learning Digest" in html_response.text


def test_learning_report_route_404_when_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    client = TestClient(main.app)
    response = client.get("/learning/report/latest")

    assert response.status_code == 404

    dashboard_response = client.get("/learning/report/latest/dashboard")
    assert dashboard_response.status_code == 404
    digest_response = client.get("/learning/report/latest/digest")
    assert digest_response.status_code == 404
    digest_dashboard_response = client.get("/learning/report/latest/digest/dashboard")
    assert digest_dashboard_response.status_code == 404


def test_learning_diagnostics_route_returns_summary(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.05,
        risk_score=0.50,
        confidence_score=0.20,
        creator_score=0.0,
        lifecycle="dex",
    )

    client = TestClient(main.app)
    response = client.get("/learning/diagnostics/summary?hours=24")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts_by_decision"]["candidate_gate_skip"] == 1


def test_learning_engine_health_routes_return_status(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.05,
        risk_score=0.50,
        confidence_score=0.20,
        creator_score=0.0,
        lifecycle="dex",
        ts_value=1_773_500_000,
    )

    client = TestClient(main.app)

    json_response = client.get("/learning/health?hours=10000")
    assert json_response.status_code == 200
    payload = json_response.json()
    assert payload["status"] in {"cold", "quiet", "processing", "gated", "blocked", "active"}
    assert "skip_pressure" in payload

    html_response = client.get("/learning/health/dashboard?hours=10000")
    assert html_response.status_code == 200
    assert "Engine Health" in html_response.text


def test_learning_tuning_proposals_route_returns_config_suggestions(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SERVICE", "worker")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SHA", "auto123")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_ENV", "production")
    monkeypatch.setenv("SIGNAL_ENGINE_REQUIRED_ALIGNED_PROFILES", "strict")
    sls.init()

    base_ts = 1_773_620_000
    positive_ids = []
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
                    '{"outcome_label":"worked"}',
                ),
            )

    client = TestClient(main.app)
    response = client.get("/learning/tuning/proposals?hours=10000")

    assert response.status_code == 200
    payload = response.json()
    assert "proposals" in payload
    assert payload["proposal_count"] >= 1

    dashboard_response = client.get("/learning/tuning/proposals/dashboard?hours=10000")
    assert dashboard_response.status_code == 200
    assert "Tuning Proposals" in dashboard_response.text
    assert ".env Snippet" in dashboard_response.text
    assert "Apply Manually Diff" in dashboard_response.text

    env_response = client.get("/learning/tuning/proposals/env?hours=10000")
    assert env_response.status_code == 200
    assert "text/plain" in env_response.headers["content-type"]
    assert "EARLY_ATTENTION_MIN=" in env_response.text

    diff_response = client.get("/learning/tuning/proposals/diff?hours=10000")
    assert diff_response.status_code == 200
    assert "text/plain" in diff_response.headers["content-type"]
    assert "EARLY_ATTENTION_MIN:" in diff_response.text

    profiles_response = client.get("/learning/tuning/profiles?hours=10000")
    assert profiles_response.status_code == 200
    profiles_payload = profiles_response.json()
    assert profiles_payload["base_profile"] == "balanced"
    assert "strict" in profiles_payload["profiles"]

    profiles_dashboard_response = client.get("/learning/tuning/profiles/dashboard?hours=10000")
    assert profiles_dashboard_response.status_code == 200
    assert "Tuning Profiles" in profiles_dashboard_response.text
    assert "Balanced" in profiles_dashboard_response.text

    strict_env_response = client.get("/learning/tuning/profiles/strict/env?hours=10000")
    assert strict_env_response.status_code == 200
    assert "PROM_MIN_LIQ_USD=" in strict_env_response.text

    aggressive_diff_response = client.get("/learning/tuning/profiles/aggressive/diff?hours=10000")
    assert aggressive_diff_response.status_code == 200
    assert "EARLY_ATTENTION_MIN:" in aggressive_diff_response.text

    create_approval_response = client.post(
        "/learning/tuning/approvals",
        json={
            "approval_kind": "profile",
            "target_name": "strict",
            "artifact_kind": "env",
            "hours": 10000,
            "approved_by": "ops",
            "notes": "ready for manual rollout",
        },
    )
    assert create_approval_response.status_code == 200
    approval_payload = create_approval_response.json()
    assert approval_payload["approval_kind"] == "profile"
    assert approval_payload["target_name"] == "strict"
    assert approval_payload["rollout_status"] == "pending"

    approvals_response = client.get("/learning/tuning/approvals?limit=10")
    assert approvals_response.status_code == 200
    approvals_payload = approvals_response.json()
    assert approvals_payload["approvals"]

    approval_id = approval_payload["approval_id"]
    approve_response = client.post(
        f"/learning/tuning/approvals/{approval_id}/status",
        json={"rollout_status": "approved", "notes": "approved for deploy"},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["rollout_status"] == "approved"

    status_response = client.post(
        f"/learning/tuning/approvals/{approval_id}/status",
        json={
            "rollout_status": "rolled_out",
            "notes": "rolled to render",
        },
    )
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["rollout_status"] == "rolled_out"
    assert status_payload["deployment_service"] == "worker"
    assert status_payload["deployment_sha"] == "auto123"

    latest_response = client.get(
        "/learning/tuning/approvals/latest?approval_kind=profile&target_name=strict&artifact_kind=env&rollout_status=rolled_out"
    )
    assert latest_response.status_code == 200
    latest_payload = latest_response.json()
    assert latest_payload["approval_id"] == approval_id

    latest_artifact_response = client.get(
        "/learning/tuning/approvals/latest/artifact?approval_kind=profile&target_name=strict&artifact_kind=env&rollout_status=rolled_out"
    )
    assert latest_artifact_response.status_code == 200
    assert "PROM_MIN_LIQ_USD=" in latest_artifact_response.text

    filtered_approvals_response = client.get("/learning/tuning/approvals?limit=10&rollout_status=rolled_out&q=render")
    assert filtered_approvals_response.status_code == 200
    filtered_payload = filtered_approvals_response.json()
    assert filtered_payload["approvals"]

    latest_bundle_response = client.get("/learning/tuning/approvals/latest/bundle?artifact_kind=env&rollout_status=rolled_out")
    assert latest_bundle_response.status_code == 200
    assert "[strict]" in latest_bundle_response.text

    drift_response = client.get("/learning/tuning/drift?target_name=strict&rollout_status=rolled_out")
    assert drift_response.status_code == 200
    drift_payload = drift_response.json()
    assert drift_payload["target_name"] == "strict"
    assert "drift_count" in drift_payload

    rollout_summary_response = client.get("/learning/tuning/rollout/summary")
    assert rollout_summary_response.status_code == 200
    rollout_summary_payload = rollout_summary_response.json()
    assert rollout_summary_payload["latest_by_service"]["worker"]["approval_id"] == approval_id
    assert rollout_summary_payload["notifications"]
    assert rollout_summary_payload["recommended_actions"]

    rollout_dashboard_response = client.get("/learning/tuning/rollout/dashboard")
    assert rollout_dashboard_response.status_code == 200
    assert "Tuning Rollout Summary" in rollout_dashboard_response.text
    assert "Recommended Actions" in rollout_dashboard_response.text

    notifications_response = client.get("/learning/tuning/notifications?limit=20")
    assert notifications_response.status_code == 200
    notifications_payload = notifications_response.json()
    assert notifications_payload["notifications"]
    assert any(item["event_type"] == "drift_resolved" for item in notifications_payload["notifications"])

    active_notifications_response = client.get("/learning/tuning/notifications?limit=20&active_only=true")
    assert active_notifications_response.status_code == 200
    active_notifications_payload = active_notifications_response.json()
    assert active_notifications_payload["notifications"]

    notifications_dashboard_response = client.get("/learning/tuning/notifications/dashboard?limit=20")
    assert notifications_dashboard_response.status_code == 200
    assert "Rollout Notifications" in notifications_dashboard_response.text
    assert "Active" in notifications_dashboard_response.text

    first_notification_id = notifications_payload["notifications"][0]["notification_id"]
    ack_response = client.post(
        f"/learning/tuning/notifications/{first_notification_id}/state",
        json={"acknowledged": True, "acknowledged_by": "ops-user"},
    )
    assert ack_response.status_code == 200
    ack_payload = ack_response.json()
    assert ack_payload["acknowledged_by"] == "ops-user"

    active_after_ack = client.get("/learning/tuning/notifications?limit=20&active_only=true")
    assert active_after_ack.status_code == 200
    assert all(item["notification_id"] != first_notification_id for item in active_after_ack.json()["notifications"])

    unsnooze_response = client.post(
        f"/learning/tuning/notifications/{first_notification_id}/state",
        json={"acknowledged": False, "snooze_minutes": 15},
    )
    assert unsnooze_response.status_code == 200
    assert unsnooze_response.json()["snoozed_until_ts"] is not None

    command_center_response = client.get("/learning/command-center?hours=24")
    assert command_center_response.status_code == 200
    command_center_payload = command_center_response.json()
    assert "engine_health" in command_center_payload
    assert "rollout_summary" in command_center_payload
    assert "drift" in command_center_payload

    command_center_dashboard_response = client.get("/learning/command-center/dashboard?hours=24")
    assert command_center_dashboard_response.status_code == 200
    assert "Operator Command Center" in command_center_dashboard_response.text
    assert "Health Snapshot" in command_center_dashboard_response.text

    ops_digest_response = client.get("/learning/ops/digest?hours=24")
    assert ops_digest_response.status_code == 200
    ops_digest_payload = ops_digest_response.json()
    assert "severity" in ops_digest_payload
    assert "summary" in ops_digest_payload

    ops_digest_dashboard_response = client.get("/learning/ops/digest/dashboard?hours=24")
    assert ops_digest_dashboard_response.status_code == 200
    assert "Ops Digest" in ops_digest_dashboard_response.text

    ops_digest_text_response = client.get("/learning/ops/digest/text?hours=24")
    assert ops_digest_text_response.status_code == 200
    assert "Signal Engine Ops Digest" in ops_digest_text_response.text

    ops_digest_send_response = client.post("/learning/ops/digest/send", json={"hours": 24, "force": True})
    assert ops_digest_send_response.status_code == 200
    ops_digest_send_payload = ops_digest_send_response.json()
    assert ops_digest_send_payload["dispatched"] is True
    assert ops_digest_send_payload["notification"]["event_type"] == "ops_digest"

    ops_digest_cooldown_response = client.post("/learning/ops/digest/send", json={"hours": 24})
    assert ops_digest_cooldown_response.status_code == 200
    ops_digest_cooldown_payload = ops_digest_cooldown_response.json()
    assert ops_digest_cooldown_payload["dispatched"] is False
    assert ops_digest_cooldown_payload["reason"] == "cooldown_unchanged_digest"

    approvals_dashboard_response = client.get("/learning/tuning/approvals/dashboard?limit=10&rollout_status=rolled_out&q=render")
    assert approvals_dashboard_response.status_code == 200
    assert "Tuning Approvals" in approvals_dashboard_response.text
    assert "Config Drift / Strict" in approvals_dashboard_response.text
    assert "worker" in approvals_dashboard_response.text

    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SERVICE", "engine")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SHA", "diff999")
    second_create_response = client.post(
        "/learning/tuning/approvals",
        json={
            "approval_kind": "profile",
            "target_name": "strict",
            "artifact_kind": "env",
            "hours": 10000,
            "approved_by": "ops",
            "notes": "engine rollout candidate",
        },
    )
    assert second_create_response.status_code == 200
    second_id = second_create_response.json()["approval_id"]
    second_approve_response = client.post(
        f"/learning/tuning/approvals/{second_id}/status",
        json={"rollout_status": "approved", "notes": "engine approved"},
    )
    assert second_approve_response.status_code == 200
    blocked_rollout_response = client.post(
        f"/learning/tuning/approvals/{second_id}/status",
        json={"rollout_status": "rolled_out", "notes": "should block"},
    )
    assert blocked_rollout_response.status_code == 400
    assert blocked_rollout_response.json()["detail"] == "alignment_guardrail_blocked"

    notifications_after_block = client.get("/learning/tuning/notifications?limit=30")
    assert notifications_after_block.status_code == 200
    blocked_payload = notifications_after_block.json()
    assert any(item["event_type"] == "rollout_blocked" for item in blocked_payload["notifications"])


def test_learning_diagnostics_dashboard_returns_html(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.05,
        risk_score=0.50,
        confidence_score=0.20,
        creator_score=0.0,
        lifecycle="dex",
    )

    client = TestClient(main.app)
    response = client.get("/learning/diagnostics/dashboard?hours=24")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Signal Diagnostics" in response.text
    assert "Top Skip Reasons" in response.text


def test_learning_diagnostics_routes_include_outcome_sections(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_id = "sig-1"
    with sls._connect() as c:
        c.execute(
            """
            INSERT INTO signals (
                signal_id, external_ref, token, event_type, source, creator, alert_ts, updated_ts,
                lifecycle, confidence_score, attention_score, risk_score, elite_score,
                market_cap_usd, liquidity_usd, volume_m5_usd, age_minutes, price_change_m5,
                price_change_h1, txns_m5_buys, txns_m5_sells,
                hour_utc, day_of_week_utc, is_weekend_utc, hour_local, day_of_week_local,
                local_daypart, session_bucket, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                "ext-1",
                "token-a",
                "candidate",
                "test",
                None,
                1_773_500_000,
                1_773_500_000,
                "dex",
                0.4,
                0.18,
                0.2,
                7,
                10000,
                4000,
                2000,
                4.0,
                12.0,
                25.0,
                30,
                12,
                14,
                5,
                0,
                9,
                5,
                "morning",
                "us_day",
                "{}",
            ),
        )
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
                1_773_503_600,
                "dex",
                18000,
                4100,
                2600,
                64.0,
                25.0,
                60.0,
                44,
                20,
                80.0,
                2.5,
                30.0,
                "worked",
                '{"outcome_label":"worked"}',
            ),
        )
    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.18,
        risk_score=0.20,
        confidence_score=0.40,
        creator_score=0.0,
        lifecycle="dex",
        ts_value=1_773_500_000,
    )

    client = TestClient(main.app)

    summary_response = client.get("/learning/diagnostics/summary?hours=10000")
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["false_negatives"]
    assert "conversion" in summary_payload
    assert "reason_quality" in summary_payload
    assert "threshold_guidance" in summary_payload
    assert "session_signal_quality" in summary_payload
    assert "reason_trends" in summary_payload
    assert "session_signal_trends" in summary_payload

    dashboard_response = client.get("/learning/diagnostics/dashboard?hours=10000")
    assert dashboard_response.status_code == 200
    assert "False Negatives" in dashboard_response.text
    assert "Session Outcome Quality" in dashboard_response.text
    assert "Session x Signal Quality" in dashboard_response.text
    assert "Blocker Outcome Scorecards" in dashboard_response.text
    assert "Threshold Guidance" in dashboard_response.text
    assert "Blocker Trends" in dashboard_response.text
    assert "Session x Signal Trends" in dashboard_response.text


def test_learning_report_dashboard_by_date_returns_tuning_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    report = sls.generate_daily_learning_report("2026-03-14")

    client = TestClient(main.app)
    response = client.get(f"/learning/report/{report['report_date']}/dashboard")

    assert response.status_code == 200
    assert "Daily Learning Report" in response.text
    assert "Top Blockers" in response.text
    assert "Threshold Calls" in response.text

    digest_response = client.get(f"/learning/report/{report['report_date']}/digest")
    assert digest_response.status_code == 200
    digest_payload = digest_response.json()
    assert "highlights" in digest_payload

    digest_dashboard_response = client.get(f"/learning/report/{report['report_date']}/digest/dashboard")
    assert digest_dashboard_response.status_code == 200
    assert "Learning Digest" in digest_dashboard_response.text

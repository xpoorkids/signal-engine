from __future__ import annotations

import json

from app.services import signal_learning_service as sls
from worker.events import Event


def test_record_signal_event_persists_signal_and_jobs(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        creator="creator_1",
        confidence=0.61,
        ts=1_710_000_000,
        extra={
            "lifecycle": "dex",
            "attention_score": 0.66,
            "risk_score": 0.31,
            "elite_score": 8,
            "dex_summary": {
                "market_cap": 25000,
                "liquidity_usd": 8000,
                "volume_m5": 6000,
                "age_minutes": 8.2,
                "price_change_m5": 22.0,
                "price_change_h1": 80.0,
                "txns_m5_buys": 120,
                "txns_m5_sells": 55,
            },
        },
    )

    signal_id = sls.record_signal_event(event, external_ref="msg-1")

    with sls._connect() as c:
        signal_row = c.execute(
            "SELECT token, event_type, external_ref, attention_score, risk_score, session_bucket FROM signals WHERE signal_id=?",
            (signal_id,),
        ).fetchone()
        job_count = c.execute(
            "SELECT COUNT(1) FROM signal_snapshot_jobs WHERE signal_id=?",
            (signal_id,),
        ).fetchone()[0]

    assert signal_row[0] == event.token
    assert signal_row[1] == "candidate"
    assert signal_row[2] == "msg-1"
    assert signal_row[3] == 0.66
    assert signal_row[4] == 0.31
    assert signal_row[5] in {"asia", "europe", "us_day", "late_us"}
    assert job_count == len(sls.SNAPSHOT_HORIZONS_MINUTES)


def test_record_signal_event_updates_existing_external_ref(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    base_event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.50,
        extra={"attention_score": 0.55, "risk_score": 0.20},
    )
    signal_id = sls.record_signal_event(base_event, external_ref="candidate-42")

    updated_event = Event(
        type="candidate",
        source="test",
        token=base_event.token,
        confidence=0.72,
        extra={"attention_score": 0.70, "risk_score": 0.28},
    )
    updated_signal_id = sls.record_signal_event(updated_event, external_ref="candidate-42", edited=True)

    with sls._connect() as c:
        row = c.execute(
            "SELECT COUNT(1), confidence_score, attention_score, risk_score FROM signals WHERE signal_id=?",
            (signal_id,),
        ).fetchone()

    assert updated_signal_id == signal_id
    assert row[0] == 1
    assert row[1] == 0.72
    assert row[2] == 0.70
    assert row[3] == 0.28


def test_generate_daily_learning_report_summarizes_outcomes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    report_date = "2026-03-14"
    ts_value = 1_773_446_400  # 2026-03-14 00:00:00 UTC

    event = Event(
        type="promoted",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.84,
        ts=ts_value + 3600,
        extra={
            "lifecycle": "dex",
            "attention_score": 0.83,
            "risk_score": 0.18,
            "elite_score": 11,
            "dex_summary": {
                "market_cap": 10000,
                "liquidity_usd": 5000,
                "volume_m5": 4000,
                "age_minutes": 5,
                "price_change_m5": 30,
                "price_change_h1": 90,
                "txns_m5_buys": 80,
                "txns_m5_sells": 30,
            },
        },
    )
    signal_id = sls.record_signal_event(event)

    with sls._connect() as c:
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
                ts_value + 7200,
                "dex",
                18000,
                6500,
                9000,
                65,
                12,
                55,
                110,
                60,
                80.0,
                30.0,
                125.0,
                "strong_continuation",
                json.dumps({"outcome_label": "strong_continuation"}),
            ),
        )
    sls.record_signal_decision(
        token=event.token,
        event_type="promoted",
        stage="promoted",
        decision="promotion_block",
        reasons=["buyers_low", "attention<0.20"],
        attention_score=0.83,
        risk_score=0.18,
        confidence_score=0.84,
        lifecycle="dex",
        ts_value=ts_value + 3600,
        signal_id=signal_id,
        source="test",
    )

    report = sls.generate_daily_learning_report(report_date)

    assert report["report_date"] == report_date
    assert report["totals_by_type"]["promoted"] == 1
    assert report["outcomes_by_label"]["strong_continuation"] == 1
    assert report["sessions"]
    assert report["tuning_snapshot"]["top_blockers"][0]["reason"] in {"attention<0.20", "buyers_low"}
    assert report["tuning_snapshot"]["best_session_signal"]
    assert "top_relax_calls" in report["tuning_snapshot"]
    assert "worst_session_signal" in report["tuning_snapshot"]

    with sls._connect() as c:
        stored = c.execute(
            "SELECT report_json FROM learning_reports WHERE report_date=?",
            (report_date,),
        ).fetchone()
    assert stored is not None


def test_diagnostics_summary_aggregates_decisions(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20", "dex_gate:liq<12000.0"],
        attention_score=0.06,
        risk_score=0.50,
        confidence_score=0.27,
        creator_score=0.0,
        lifecycle="dex",
        ts_value=1_773_500_000,
    )
    sls.record_signal_decision(
        token="token-b",
        event_type="candidate",
        stage="promoted",
        decision="promotion_block",
        reasons=["buyers_low"],
        attention_score=0.72,
        risk_score=0.22,
        confidence_score=0.81,
        creator_score=0.4,
        lifecycle="dex",
        ts_value=1_773_500_100,
    )

    summary = sls.get_diagnostics_summary(hours=10_000)

    assert summary["counts_by_decision"]["candidate_gate_skip"] == 1
    assert summary["counts_by_decision"]["promotion_block"] == 1
    reasons = {item["reason"]: item["count"] for item in summary["top_skip_reasons"]}
    assert reasons["attention<0.20"] == 1
    assert reasons["buyers_low"] == 1


def test_diagnostics_and_health_bootstrap_schema_on_fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh-engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setattr(sls, "_SCHEMA_READY", False)

    summary = sls.get_diagnostics_summary(hours=24)
    assert summary["counts_by_decision"] == {}
    assert summary["top_skip_reasons"] == []

    health = sls.get_engine_health_digest(hours=24)
    assert health["status"] in {"cold", "quiet", "processing", "gated", "blocked", "active"}
    assert health["storage"]["db_path"]
    assert health["storage"]["decision_count"] == 0


def test_diagnostics_summary_includes_outcome_analysis(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    candidate_event = Event(
        type="candidate",
        source="test",
        token="token-missed",
        confidence=0.42,
        ts=1_773_500_000,
        extra={
            "lifecycle": "dex",
            "attention_score": 0.19,
            "risk_score": 0.24,
            "dex_summary": {"market_cap": 10000, "liquidity_usd": 4000, "volume_m5": 2000},
        },
    )
    candidate_signal_id = sls.record_signal_event(candidate_event)
    sls.record_signal_decision(
        token="token-missed",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.19,
        risk_score=0.24,
        confidence_score=0.42,
        lifecycle="dex",
        ts_value=1_773_500_000,
    )

    promoted_event = Event(
        type="promoted",
        source="test",
        token="token-bad-send",
        confidence=0.77,
        ts=1_773_500_100,
        extra={
            "lifecycle": "dex",
            "attention_score": 0.68,
            "risk_score": 0.58,
            "dex_summary": {"market_cap": 12000, "liquidity_usd": 5000, "volume_m5": 3000},
        },
    )
    promoted_signal_id = sls.record_signal_event(promoted_event)
    candidate_then_promoted = Event(
        type="candidate",
        source="test",
        token="token-convert",
        confidence=0.61,
        ts=1_773_500_200,
        extra={"lifecycle": "dex", "attention_score": 0.55, "risk_score": 0.22},
    )
    sls.record_signal_event(candidate_then_promoted)
    converted_promoted = Event(
        type="promoted",
        source="test",
        token="token-convert",
        confidence=0.82,
        ts=1_773_500_260,
        extra={"lifecycle": "dex", "attention_score": 0.71, "risk_score": 0.18},
    )
    sls.record_signal_event(converted_promoted)

    with sls._connect() as c:
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
                candidate_signal_id,
                60,
                1_773_503_600,
                "dex",
                18000,
                4200,
                4500,
                60,
                35,
                80,
                100,
                40,
                80.0,
                5.0,
                125.0,
                "strong_continuation",
                json.dumps({"outcome_label": "strong_continuation"}),
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
                promoted_signal_id,
                60,
                1_773_503_700,
                "dex",
                7000,
                2900,
                900,
                65,
                -25,
                -45,
                30,
                85,
                -41.7,
                -42.0,
                -70.0,
                "failed",
                json.dumps({"outcome_label": "failed"}),
            ),
        )

    summary = sls.get_diagnostics_summary(hours=10_000)
    recommendations = sls.get_diagnostics_recommendations(hours=10_000)

    assert summary["outcomes_by_label"]["strong_continuation"] == 1
    assert summary["outcomes_by_label"]["failed"] == 1
    assert summary["false_negatives"][0]["token"] == "token-missed"
    assert summary["false_positives"][0]["token"] == "token-bad-send"
    assert summary["conversion"]["candidate_tokens"] >= 2
    assert summary["conversion"]["promoted_tokens"] >= 2
    assert summary["conversion"]["candidate_to_promoted_tokens"] >= 1
    assert summary["session_quality"]
    assert any(item["title"] == "False Negatives Detected" for item in recommendations)


def test_record_signal_decision_creates_reusable_signal_shell(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-shell",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.18,
        risk_score=0.21,
        confidence_score=0.33,
        lifecycle="dex",
        ts_value=1_773_600_000,
        source="test",
        creator="creator-shell",
    )

    assert signal_id is not None

    with sls._connect() as c:
        decision_row = c.execute(
            "SELECT signal_id FROM signal_decisions WHERE token='token-shell' ORDER BY created_ts DESC LIMIT 1"
        ).fetchone()
        signal_row = c.execute(
            "SELECT signal_id, token, event_type, source, creator, confidence_score, attention_score, risk_score FROM signals WHERE signal_id=?",
            (signal_id,),
        ).fetchone()
        job_count = c.execute(
            "SELECT COUNT(1) FROM signal_snapshot_jobs WHERE signal_id=?",
            (signal_id,),
        ).fetchone()[0]

    assert decision_row[0] == signal_id
    assert signal_row[0] == signal_id
    assert signal_row[1] == "token-shell"
    assert signal_row[2] == "candidate"
    assert signal_row[3] == "test"
    assert signal_row[4] == "creator-shell"
    assert signal_row[5] == 0.33
    assert signal_row[6] == 0.18
    assert signal_row[7] == 0.21
    assert job_count == len(sls.SNAPSHOT_HORIZONS_MINUTES)

    event = Event(
        type="candidate",
        source="test",
        token="token-shell",
        creator="creator-shell",
        confidence=0.61,
        ts=1_773_600_030,
        extra={
            "_signal_id": signal_id,
            "lifecycle": "dex",
            "attention_score": 0.55,
            "risk_score": 0.19,
            "dex_summary": {"market_cap": 18000, "liquidity_usd": 7000, "volume_m5": 5000},
        },
    )
    updated_signal_id = sls.record_signal_event(event, external_ref="candidate-message-1")

    with sls._connect() as c:
        updated_row = c.execute(
            "SELECT signal_id, external_ref, confidence_score, attention_score, risk_score, market_cap_usd FROM signals WHERE signal_id=?",
            (signal_id,),
        ).fetchone()

    assert updated_signal_id == signal_id
    assert updated_row[0] == signal_id
    assert updated_row[1] == "candidate-message-1"
    assert updated_row[2] == 0.61
    assert updated_row[3] == 0.55
    assert updated_row[4] == 0.19
    assert updated_row[5] == 18000


def test_record_signal_decision_persists_policy_trace_metadata(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-trace",
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        reasons=["improved_attention"],
        features={
            "attention_score": 0.74,
            "creator_score": 0.42,
            "candidate_rate_limit_allowed": True,
            "candidate_progression_ok": True,
            "candidate_send_eligible": True,
        },
        attention_score=0.74,
        risk_score=0.18,
        confidence_score=0.67,
        creator_score=0.42,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="policy-live-1",
        ts_value=1_773_600_500,
        source="test",
    )

    with sls._connect() as c:
        row = c.execute(
            """
            SELECT signal_id, action_taken, policy_name, policy_version, features_json
            FROM signal_decisions
            WHERE signal_id=?
            """,
            (signal_id,),
        ).fetchone()

    assert row[0] == signal_id
    assert row[1] == "emit"
    assert row[2] == "deterministic_engine"
    assert row[3] == "policy-live-1"
    features = json.loads(row[4])
    assert features["attention_score"] == 0.74
    assert features["candidate_progression_ok"] is True


def test_evaluate_shadow_policy_reports_action_changes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-shadow",
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        reasons=["improved_attention"],
        features={
            "attention_score": 0.74,
            "creator_score": 0.42,
            "candidate_rate_limit_allowed": True,
            "candidate_progression_ok": True,
            "candidate_send_eligible": True,
        },
        attention_score=0.74,
        risk_score=0.18,
        confidence_score=0.67,
        creator_score=0.42,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="policy-live-1",
        ts_value=1_773_600_500,
        source="test",
    )

    with sls._connect() as c:
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
                1_773_604_100,
                "dex",
                22000,
                6400,
                4500,
                60.0,
                28.0,
                70.0,
                48,
                20,
                85.0,
                5.0,
                30.0,
                "worked",
                json.dumps({"outcome_label": "worked"}),
            ),
        )

    result = sls.evaluate_shadow_policy(
        hours=10_000,
        policy_name="shadow_policy",
        policy_version="shadow-2",
        overrides={"candidate_attention_min": 0.80},
    )

    assert result["changed_count"] == 1
    assert result["changed_examples"][0]["current_action"] == "emit"
    assert result["changed_examples"][0]["shadow_action"] == "hold"
    assert result["impact"]["positive_outcomes"] == 1


def test_run_policy_replay_persists_run_and_results(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-replay",
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        reasons=["improved_attention"],
        features={
            "attention_score": 0.74,
            "creator_score": 0.42,
            "candidate_rate_limit_allowed": True,
            "candidate_progression_ok": True,
            "candidate_send_eligible": True,
        },
        attention_score=0.74,
        risk_score=0.18,
        confidence_score=0.67,
        creator_score=0.42,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="policy-live-1",
        ts_value=1_773_600_500,
        source="test",
    )
    with sls._connect() as c:
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
                1_773_604_100,
                "dex",
                22000,
                6400,
                4500,
                60.0,
                28.0,
                70.0,
                48,
                20,
                85.0,
                5.0,
                30.0,
                "worked",
                json.dumps({"outcome_label": "worked"}),
            ),
        )

    replay = sls.run_policy_replay(
        hours=10_000,
        stage="candidate",
        policy_name="shadow_policy",
        policy_version="shadow-3",
        overrides={"candidate_attention_min": 0.80},
    )

    stored = sls.get_policy_replay(replay["run_id"])
    latest = sls.get_latest_policy_replay()

    assert replay["changed_count"] == 1
    assert stored is not None
    assert stored["run_id"] == replay["run_id"]
    assert stored["results"][0]["changed"] is True
    assert latest is not None
    assert latest["run_id"] == replay["run_id"]


def test_policy_profiles_and_rollouts_resolve_live_policy(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    profile = sls.create_policy_profile(
        policy_name="adaptive_candidate",
        policy_version="v1",
        config={"candidate_creator_min": 0.55, "promoted_liquidity_min": 22000.0},
        description="candidate strict profile",
        created_by="ops",
    )
    rollout = sls.activate_policy_rollout(
        policy_name="adaptive_candidate",
        policy_version="v1",
        rollout_mode="active",
        stage_scope="candidate",
        traffic_percent=100,
        priority=10,
        activated_by="ops",
    )
    resolved = sls.resolve_live_policy("candidate", token="token-123")
    default_promoted = sls.resolve_live_policy("promoted", token="token-123")

    assert profile["policy_name"] == "adaptive_candidate"
    assert rollout["rollout_mode"] == "active"
    assert resolved["policy_name"] == "adaptive_candidate"
    assert resolved["config"]["candidate_creator_min"] == 0.55
    assert default_promoted["policy_name"]


def test_canary_rollout_resolution_respects_token_bucket(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    sls.create_policy_profile(
        policy_name="canary_policy",
        policy_version="v1",
        config={"promoted_buyers_15m_min": 99},
        created_by="ops",
    )
    sls.activate_policy_rollout(
        policy_name="canary_policy",
        policy_version="v1",
        rollout_mode="canary",
        stage_scope="promoted",
        traffic_percent=5,
        priority=1,
        activated_by="ops",
    )

    resolved_low = sls.resolve_live_policy("promoted", token="")
    resolved_high = sls.resolve_live_policy("promoted", token="zzzzzz")

    assert resolved_low["policy_name"] == "canary_policy"
    assert resolved_high["policy_name"] != "canary_policy"


def test_policy_approval_and_guardrail_rollback_flow(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    sls.create_policy_profile(
        policy_name="canary_guarded",
        policy_version="v1",
        config={"promoted_risk_max": 0.45},
        created_by="ops",
    )
    replay = sls.run_policy_replay(
        hours=24,
        stage="promoted",
        policy_name="canary_guarded",
        policy_version="v1",
        overrides={"promoted_risk_max": 0.45},
    )
    approval = sls.create_policy_approval(
        policy_name="canary_guarded",
        policy_version="v1",
        source_type="replay",
        source_ref=replay["run_id"],
        approved_by="ops",
    )
    rollout = sls.activate_policy_rollout(
        policy_name="canary_guarded",
        policy_version="v1",
        rollout_mode="canary",
        stage_scope="promoted",
        traffic_percent=100,
        priority=1,
        activated_by="ops",
    )

    for idx in range(3):
        signal_id = sls.record_signal_decision(
            token=f"token-canary-{idx}",
            event_type="promoted",
            stage="promoted",
            decision="promoted_sent",
            action_taken="emit",
            attention_score=0.7,
            risk_score=0.5,
            confidence_score=0.9,
            creator_score=0.3,
            lifecycle="dex",
            policy_name="canary_guarded",
            policy_version="v1",
            ts_value=1_773_700_000 + idx,
            source="test",
        )
        with sls._connect() as c:
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
                    1_773_703_600 + idx,
                    "dex",
                    7000,
                    2500,
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

    approval_after = sls.update_policy_approval_status(
        approval["approval_id"],
        approval_status="rolled_out",
        notes="canary started",
    )
    guardrails = sls.evaluate_policy_guardrails(hours=10000, min_samples=3, max_negative_rate=60.0, auto_apply=True)
    events = sls.list_policy_rollout_events(limit=20)
    rollouts = sls.list_policy_rollouts(limit=20, active_only=False)

    assert approval_after["approval_status"] == "rolled_out"
    assert guardrails["evaluations"][0]["recommended_action"] == "rollback"
    assert guardrails["evaluations"][0]["applied"] is True
    assert any(item["event_type"] == "guardrail_rollback" for item in events)
    matching_rollout = next(item for item in rollouts if item["rollout_id"] == rollout["rollout_id"])
    assert matching_rollout["rollout_status"] == "rolled_back"


def test_policy_automation_cycle_auto_promotes_stable_canary(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    sls.create_policy_profile(
        policy_name="baseline_policy",
        policy_version="v1",
        config={"promoted_risk_max": 0.60},
        created_by="ops",
    )
    sls.activate_policy_rollout(
        policy_name="baseline_policy",
        policy_version="v1",
        rollout_mode="active",
        stage_scope="promoted",
        traffic_percent=100,
        priority=20,
        activated_by="ops",
    )
    sls.create_policy_profile(
        policy_name="auto_policy",
        policy_version="v2",
        config={"promoted_risk_max": 0.40},
        created_by="ops",
    )

    signal_id = sls.record_signal_decision(
        token="token-auto-replay",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.82,
        risk_score=0.31,
        confidence_score=0.92,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="baseline_policy",
        policy_version="v1",
        features={
            "attention_score": 0.82,
            "risk_score": 0.31,
            "confidence_score": 0.92,
            "liquidity_usd": 22000.0,
            "txns_m5_buys": 22,
        },
        ts_value=1_773_800_000,
        source="test",
    )
    with sls._connect() as c:
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
                1_773_803_600,
                "dex",
                21000,
                11000,
                7500,
                80.0,
                18.0,
                45.0,
                48,
                16,
                60.0,
                20.0,
                35.0,
                "worked",
                json.dumps({"outcome_label": "worked"}),
            ),
        )

    replay = sls.run_policy_replay(
        hours=10_000,
        stage="promoted",
        policy_name="auto_policy",
        policy_version="v2",
        overrides={"promoted_risk_max": 0.20},
    )

    for idx in range(3):
        canary_signal_id = sls.record_signal_decision(
            token=f"token-auto-canary-{idx}",
            event_type="promoted",
            stage="promoted",
            decision="promoted_sent",
            action_taken="emit",
            attention_score=0.86,
            risk_score=0.22,
            confidence_score=0.94,
            creator_score=0.35,
            lifecycle="dex",
            policy_name="auto_policy",
            policy_version="v2",
            ts_value=1_773_810_000 + idx,
            source="test",
        )
        with sls._connect() as c:
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
                    canary_signal_id,
                    60,
                    1_773_813_600 + idx,
                    "dex",
                    26000,
                    12000,
                    8000,
                    70.0,
                    24.0,
                    60.0,
                    60,
                    18,
                    75.0,
                    18.0,
                    40.0,
                    "strong_continuation",
                    json.dumps({"outcome_label": "strong_continuation"}),
                ),
            )

    cycle = sls.run_policy_automation_cycle(hours=10_000, replay_limit=10)
    rollouts = sls.list_policy_rollouts(limit=20, active_only=False)
    events = sls.list_policy_rollout_events(limit=20)
    approvals = sls.list_policy_approvals(limit=20)
    latest_run = sls.get_latest_policy_automation_run()
    runs = sls.list_policy_automation_runs(limit=5)
    status = sls.get_policy_automation_status()

    assert replay["run_id"]
    assert "generated" in cycle
    assert cycle["approvals"]["created"]
    assert cycle["canaries"]["scheduled"]
    assert cycle["promotions"]["promoted"]
    assert latest_run is not None
    assert latest_run["run_id"] == cycle["run_id"]
    assert runs[0]["run_id"] == cycle["run_id"]
    assert status["latest_run"]["run_id"] == cycle["run_id"]
    assert any(item["event_type"] == "auto_approval_created" for item in events)
    assert any(item["event_type"] == "auto_canary_started" for item in events)
    assert any(item["event_type"] == "canary_promoted" for item in events)
    assert any(
        item["policy_name"] == "auto_policy"
        and item["policy_version"] == "v2"
        and item["rollout_mode"] == "active"
        and item["rollout_status"] == "active"
        for item in rollouts
    )
    assert any(
        item["policy_name"] == "auto_policy"
        and item["policy_version"] == "v2"
        and item["approval_status"] == "rolled_out"
        for item in approvals
    )


def test_generate_policy_candidates_creates_replayed_profiles(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    missed_signal_id = sls.record_signal_decision(
        token="token-missed",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.19,
        risk_score=0.22,
        confidence_score=0.31,
        creator_score=0.22,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "attention_score": 0.19,
            "creator_score": 0.22,
            "candidate_rate_limit_allowed": True,
            "candidate_progression_ok": True,
            "candidate_send_eligible": True,
        },
        ts_value=1_773_850_000,
        source="test",
    )
    failed_signal_id = sls.record_signal_decision(
        token="token-failed",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        reasons=["dex_gate:liq<12000.0"],
        attention_score=0.72,
        risk_score=0.62,
        confidence_score=0.88,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "attention_score": 0.72,
            "risk_score": 0.62,
            "confidence_score": 0.88,
            "liquidity_usd": 9000.0,
            "unique_buyers_15m": 18,
        },
        ts_value=1_773_850_100,
        source="test",
    )
    with sls._connect() as c:
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
                missed_signal_id,
                60,
                1_773_853_600,
                "dex",
                18000,
                5000,
                3000,
                64.0,
                25.0,
                60.0,
                44,
                20,
                80.0,
                2.5,
                30.0,
                "worked",
                json.dumps({"outcome_label": "worked"}),
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
                failed_signal_id,
                60,
                1_773_853_700,
                "dex",
                7000,
                2500,
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

    generated = sls.generate_policy_candidates(hours=10_000, generation_limit=4, replay_limit=200)
    profiles = sls.list_policy_profiles(limit=20)
    replays = sls.list_policy_replays(limit=20)

    assert generated["generated"]
    assert any(str(item["profile"]["policy_name"]).startswith("generated_") for item in generated["generated"])
    assert any(item["source_signal"] in {"false_negatives", "false_positives", "threshold_guidance"} for item in generated["generated"])
    assert any(str(item["policy_name"]).startswith("generated_") for item in profiles)
    assert any(str(item.get("shadow_policy", {}).get("policy_name") or "").startswith("generated_") for item in replays)


def test_policy_automation_safety_guardrails_limit_duplicate_and_concurrent_rollouts(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_MAX_AUTO_APPROVALS_PER_DAY", "1")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_MAX_CANARY_ROLLOUTS_PER_DAY", "2")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_MAX_ACTIVE_CANARIES_PER_STAGE", "1")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_COOLDOWN_SEC", "0")
    sls.init()

    sls.create_policy_profile(
        policy_name="dupe_policy",
        policy_version="v1",
        config={"promoted_risk_max": 0.40},
        created_by="ops",
    )
    sls.create_policy_profile(
        policy_name="dupe_policy",
        policy_version="v2",
        config={"promoted_risk_max": 0.40},
        created_by="ops",
    )
    sls.create_policy_profile(
        policy_name="other_policy",
        policy_version="v1",
        config={"promoted_risk_max": 0.35},
        created_by="ops",
    )

    baseline_signal_id = sls.record_signal_decision(
        token="token-dupe-baseline",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.80,
        risk_score=0.25,
        confidence_score=0.90,
        creator_score=0.2,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "attention_score": 0.80,
            "risk_score": 0.25,
            "confidence_score": 0.90,
            "liquidity_usd": 22000.0,
            "txns_m5_buys": 25,
        },
        ts_value=1_773_840_000,
        source="test",
    )
    with sls._connect() as c:
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
                baseline_signal_id,
                60,
                1_773_843_600,
                "dex",
                20000,
                10500,
                7000,
                70.0,
                15.0,
                40.0,
                42,
                14,
                45.0,
                12.0,
                28.0,
                "worked",
                json.dumps({"outcome_label": "worked"}),
            ),
        )

    replay_one = sls.run_policy_replay(
        hours=10_000,
        stage="promoted",
        policy_name="dupe_policy",
        policy_version="v1",
        overrides={"promoted_risk_max": 0.20},
    )
    replay_two = sls.run_policy_replay(
        hours=10_000,
        stage="promoted",
        policy_name="dupe_policy",
        policy_version="v2",
        overrides={"promoted_risk_max": 0.20},
    )
    replay_three = sls.run_policy_replay(
        hours=10_000,
        stage="promoted",
        policy_name="other_policy",
        policy_version="v1",
        overrides={"promoted_risk_max": 0.20},
    )

    approvals_result = sls.auto_create_policy_approvals(limit=10)
    created_versions = {item["policy_version"] for item in approvals_result["created"]}
    skipped_reasons = {item["reason"] for item in approvals_result["skipped"]}

    sls.update_policy_approval_status(
        next(item["approval_id"] for item in approvals_result["created"]),
        approval_status="approved",
        approved_by="ops",
        notes="approved for canary",
    )
    manual_other = sls.create_policy_approval(
        policy_name="other_policy",
        policy_version="v1",
        source_type="replay",
        source_ref=replay_three["run_id"],
        approved_by="ops",
    )
    sls.update_policy_approval_status(
        manual_other["approval_id"],
        approval_status="approved",
        approved_by="ops",
        notes="approved for canary",
    )

    canary_result = sls.auto_schedule_policy_canaries(hours=10_000)
    follow_on = sls.create_policy_profile(
        policy_name="third_policy",
        policy_version="v1",
        config={"promoted_risk_max": 0.30},
        created_by="ops",
    )
    replay_four = sls.run_policy_replay(
        hours=10_000,
        stage="promoted",
        policy_name=follow_on["policy_name"],
        policy_version=follow_on["policy_version"],
        overrides={"promoted_risk_max": 0.20},
    )
    follow_on_approval = sls.create_policy_approval(
        policy_name=follow_on["policy_name"],
        policy_version=follow_on["policy_version"],
        source_type="replay",
        source_ref=replay_four["run_id"],
        approved_by="ops",
    )
    sls.update_policy_approval_status(
        follow_on_approval["approval_id"],
        approval_status="approved",
        approved_by="ops",
        notes="approved for concurrency test",
    )
    second_canary_result = sls.auto_schedule_policy_canaries(hours=10_000)
    guardrails = sls.get_policy_automation_status()["guardrails"]

    assert replay_one["run_id"] and replay_two["run_id"] and replay_three["run_id"]
    assert len(created_versions) == 1
    assert "duplicate_profile_fingerprint" in skipped_reasons or "approval_budget_exhausted" in skipped_reasons
    assert len(canary_result["scheduled"]) == 1
    assert any(item["reason"] == "stage_canary_limit_reached" for item in second_canary_result["skipped"])
    assert int(guardrails["budgets"]["auto_approvals_used"]) >= 1
    assert int(guardrails["active_canaries_by_stage"]["promoted"]) == 1


def test_diagnostics_summary_builds_reason_quality_scorecards(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    worked_signal_id = sls.record_signal_decision(
        token="token-worked",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["buyers_low", "attention<0.20"],
        attention_score=0.18,
        risk_score=0.20,
        confidence_score=0.31,
        lifecycle="dex",
        ts_value=1_773_610_000,
        source="test",
    )
    failed_signal_id = sls.record_signal_decision(
        token="token-failed",
        event_type="candidate",
        stage="promoted",
        decision="promotion_block",
        reasons=["buyers_low", "dex_gate:liq<12000.0"],
        attention_score=0.44,
        risk_score=0.28,
        confidence_score=0.49,
        lifecycle="dex",
        ts_value=1_773_610_050,
        source="test",
    )

    with sls._connect() as c:
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
                worked_signal_id,
                60,
                1_773_613_600,
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
                json.dumps({"outcome_label": "worked"}),
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
                failed_signal_id,
                60,
                1_773_613_700,
                "dex",
                7000,
                2900,
                900,
                65,
                -25,
                -45,
                30,
                85,
                -41.7,
                -42.0,
                -70.0,
                "failed",
                json.dumps({"outcome_label": "failed"}),
            ),
        )

    summary = sls.get_diagnostics_summary(hours=10_000)
    recommendations = sls.get_diagnostics_recommendations(hours=10_000)

    scorecards = {item["reason"]: item for item in summary["reason_quality"]}
    guidance = {item["reason"]: item for item in summary["threshold_guidance"]}
    assert scorecards["buyers_low"]["total"] == 2
    assert scorecards["buyers_low"]["positive"] == 1
    assert scorecards["buyers_low"]["negative"] == 1
    assert scorecards["buyers_low"]["positive_rate"] == 50.0
    assert scorecards["dex_gate:liq<12000.0"]["negative"] == 1
    assert guidance["buyers_low"]["action"] in {"review", "hold"}
    assert guidance["buyers_low"]["sample_size"] == 2
    assert any(item["title"] == "Most Costly Blocker" for item in recommendations)
    assert any(item["title"] == "Most Protective Blocker" for item in recommendations)


def test_threshold_guidance_recommends_relax_or_tighten(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    positive_ids = []
    negative_ids = []
    base_ts = 1_773_620_000
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

    summary = sls.get_diagnostics_summary(hours=10_000)
    guidance = {item["reason"]: item for item in summary["threshold_guidance"]}
    recommendations = sls.get_diagnostics_recommendations(hours=10_000)

    assert guidance["attention<0.20"]["action"] == "relax_slightly"
    assert guidance["attention<0.20"]["confidence"] == "medium"
    assert guidance["dex_gate:liq<12000.0"]["action"] == "tighten"
    assert guidance["dex_gate:liq<12000.0"]["confidence"] == "medium"
    assert any(item["title"] == "Threshold: attention<0.20" for item in recommendations)


def test_session_signal_quality_highlights_best_and_worst_combos(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    with sls._connect() as c:
        signal_rows = [
            ("sig-a1", "token-a1", "candidate", "asia", "worked"),
            ("sig-a2", "token-a2", "candidate", "asia", "strong_continuation"),
            ("sig-u1", "token-u1", "promoted", "us_day", "failed"),
            ("sig-u2", "token-u2", "promoted", "us_day", "faded"),
        ]
        for idx, (signal_id, token, event_type, session_bucket, outcome_label) in enumerate(signal_rows):
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
                    f"ext-{idx}",
                    token,
                    event_type,
                    "test",
                    None,
                    1_773_700_000 + idx,
                    1_773_700_000 + idx,
                    "dex",
                    0.5,
                    0.4,
                    0.2,
                    8,
                    12000,
                    5000,
                    2400,
                    6.0,
                    18.0,
                    40.0,
                    40,
                    18,
                    12,
                    2,
                    0,
                    8,
                    2,
                    "morning",
                    session_bucket,
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
                    1_773_703_600 + idx,
                    "dex",
                    18000 if outcome_label in {"worked", "strong_continuation"} else 7000,
                    5200 if outcome_label in {"worked", "strong_continuation"} else 2500,
                    3000,
                    66.0,
                    20.0 if outcome_label in {"worked", "strong_continuation"} else -18.0,
                    55.0 if outcome_label in {"worked", "strong_continuation"} else -40.0,
                    55,
                    22,
                    80.0 if outcome_label in {"worked", "strong_continuation"} else -35.0,
                    4.0 if outcome_label in {"worked", "strong_continuation"} else -38.0,
                    30.0,
                    outcome_label,
                    json.dumps({"outcome_label": outcome_label}),
                ),
            )

    summary = sls.get_diagnostics_summary(hours=10_000)
    recommendations = sls.get_diagnostics_recommendations(hours=10_000)

    combos = {(item["session_bucket"], item["signal_type"]): item for item in summary["session_signal_quality"]}
    assert combos[("asia", "candidate")]["win_rate"] == 100.0
    assert combos[("us_day", "promoted")]["fail_rate"] == 100.0
    assert any(item["title"] == "Best Session x Signal" for item in recommendations)
    assert any(item["title"] == "Weakest Session x Signal" for item in recommendations)


def test_diagnostics_summary_builds_daily_trends(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    with sls._connect() as c:
        signal_specs = [
            ("sig-r1", "token-r1", "candidate", "asia", 1_773_600_000, "failed"),
            ("sig-r2", "token-r2", "candidate", "asia", 1_773_686_400, "worked"),
            ("sig-s1", "token-s1", "promoted", "us_day", 1_773_600_100, "failed"),
            ("sig-s2", "token-s2", "promoted", "us_day", 1_773_686_500, "worked"),
        ]
        for idx, (signal_id, token, event_type, session_bucket, alert_ts, outcome_label) in enumerate(signal_specs):
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
                    f"ext-trend-{idx}",
                    token,
                    event_type,
                    "test",
                    None,
                    alert_ts,
                    alert_ts,
                    "dex",
                    0.5,
                    0.4,
                    0.2,
                    8,
                    12000,
                    5000,
                    2400,
                    6.0,
                    18.0,
                    40.0,
                    40,
                    18,
                    12,
                    2,
                    0,
                    8,
                    2,
                    "morning",
                    session_bucket,
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
                    alert_ts + 3600,
                    "dex",
                    18000 if outcome_label == "worked" else 7000,
                    5200 if outcome_label == "worked" else 2500,
                    3000,
                    66.0,
                    20.0 if outcome_label == "worked" else -18.0,
                    55.0 if outcome_label == "worked" else -40.0,
                    55,
                    22,
                    80.0 if outcome_label == "worked" else -35.0,
                    4.0 if outcome_label == "worked" else -38.0,
                    30.0,
                    outcome_label,
                    json.dumps({"outcome_label": outcome_label}),
                ),
            )

    sls.record_signal_decision(
        token="token-r1",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.19,
        risk_score=0.20,
        confidence_score=0.30,
        lifecycle="dex",
        ts_value=1_773_600_000,
        signal_id="sig-r1",
        source="test",
    )
    sls.record_signal_decision(
        token="token-r2",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.19,
        risk_score=0.20,
        confidence_score=0.30,
        lifecycle="dex",
        ts_value=1_773_686_400,
        signal_id="sig-r2",
        source="test",
    )

    summary = sls.get_diagnostics_summary(hours=10_000)
    recommendations = sls.get_diagnostics_recommendations(hours=10_000)

    reason_trends = {item["reason"]: item for item in summary["reason_trends"]}
    combo_trends = {
        (item["session_bucket"], item["signal_type"]): item for item in summary["session_signal_trends"]
    }
    assert reason_trends["attention<0.20"]["win_rate_delta"] == 100.0
    assert combo_trends[("us_day", "promoted")]["win_rate_delta"] == 100.0
    assert any(item["title"] == "Improving Blocker Trend" for item in recommendations)
    assert any(item["title"] == "Improving Session x Signal" for item in recommendations)

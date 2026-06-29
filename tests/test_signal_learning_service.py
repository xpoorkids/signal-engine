from __future__ import annotations

import json

import httpx
import pytest

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


def test_due_snapshot_jobs_skip_stale_work_and_report_backlog(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_SNAPSHOT_MAX_LAG_SECONDS", "3600")
    now = 1_800_000_000
    monkeypatch.setattr(sls.time, "time", lambda: now)
    sls.init()

    event = Event(type="candidate", source="test", token="token-stale-jobs", ts=now - 300, extra={})
    signal_id = sls.record_signal_event(event, external_ref="stale-jobs")
    with sls._connect() as c:
        c.execute(
            "UPDATE signal_snapshot_jobs SET due_ts=? WHERE signal_id=? AND horizon_minutes=5",
            (now - 30, signal_id),
        )
        c.execute(
            "UPDATE signal_snapshot_jobs SET due_ts=? WHERE signal_id=? AND horizon_minutes=15",
            (now - 3601, signal_id),
        )

    jobs = sls._fetch_due_jobs(limit=10)
    storage = sls.get_learning_storage_status()

    assert [(job["signal_id"], job["horizon_minutes"]) for job in jobs] == [
        (signal_id, 0),
        (signal_id, 5),
    ]
    assert storage["snapshot_job_counts"]["pending"] == len(sls.SNAPSHOT_HORIZONS_MINUTES)
    assert storage["stale_pending_snapshot_jobs"] == 1
    assert storage["snapshot_max_lag_seconds"] == 3600


def test_prune_stale_snapshot_jobs_removes_only_old_pending_jobs(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_SNAPSHOT_MAX_LAG_SECONDS", "3600")
    now = 1_800_000_000
    monkeypatch.setattr(sls.time, "time", lambda: now)
    sls.init()

    event = Event(type="candidate", source="test", token="token-prune-jobs", ts=now - 300, extra={})
    signal_id = sls.record_signal_event(event, external_ref="prune-jobs")
    with sls._connect() as c:
        c.execute(
            "UPDATE signal_snapshot_jobs SET due_ts=? WHERE signal_id=? AND horizon_minutes=5",
            (now - 30, signal_id),
        )
        c.execute(
            "UPDATE signal_snapshot_jobs SET due_ts=? WHERE signal_id=? AND horizon_minutes=15",
            (now - 7200, signal_id),
        )
        c.execute(
            "UPDATE signal_snapshot_jobs SET status='done', due_ts=? WHERE signal_id=? AND horizon_minutes=60",
            (now - 7200, signal_id),
        )

    result = sls.prune_stale_snapshot_jobs(limit=10)

    with sls._connect() as c:
        rows = c.execute(
            "SELECT horizon_minutes, status FROM signal_snapshot_jobs WHERE signal_id=? ORDER BY horizon_minutes",
            (signal_id,),
        ).fetchall()

    assert result["deleted"] == 1
    assert result["stale_pending_before"] == 1
    assert result["stale_pending_remaining"] == 0
    row_pairs = [(int(row[0]), str(row[1])) for row in rows]
    assert (15, "pending") not in row_pairs
    assert (60, "done") in row_pairs


def test_snapshot_horizons_include_immediate_market_baseline():
    assert sls.SNAPSHOT_HORIZONS_MINUTES[0] == 0
    assert sls.SNAPSHOT_HORIZONS_MINUTES[-1] == 240


def test_signal_baseline_uses_immediate_snapshot_when_event_metrics_are_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    event = Event(type="trade_buy", source="test", token="token-baseline", ts=1_800_000_000, extra={})
    signal_id = sls.record_signal_event(event)
    with sls._connect() as c:
        c.execute(
            """
            INSERT INTO signal_snapshots (
                signal_id, horizon_minutes, captured_ts, lifecycle, market_cap_usd, liquidity_usd,
                volume_m5_usd, age_minutes, price_change_m5, price_change_h1, txns_m5_buys,
                txns_m5_sells, market_cap_change_pct, liquidity_change_pct, volume_m5_change_pct,
                outcome_label, snapshot_json
            ) VALUES (?, 0, ?, 'dex', 10000, 5000, 3000, 2, 5, 10, 20, 8, NULL, NULL, NULL, 'insufficient_data', '{}')
            """,
            (signal_id, 1_800_000_001),
        )

    baseline = sls._get_signal_baseline(signal_id)

    assert baseline is not None
    assert baseline["market_cap_usd"] == 10000
    assert baseline["liquidity_usd"] == 5000
    assert baseline["volume_m5_usd"] == 3000
    assert baseline["txns_m5_buys"] == 20


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


def test_historical_corpus_summary_aggregates_duplicates_and_feature_coverage(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_DECISION_DEDUP_WINDOW_SECONDS", "0")
    sls.init()

    event = Event(
        type="trade_buy",
        source="test",
        token="token-history",
        ts=1_800_000_000,
        extra={"attention_score": 0.4, "risk_score": 0.2},
    )
    signal_id = sls.record_signal_event(event)
    for _ in range(2):
        sls.record_signal_decision(
            token=event.token,
            event_type=event.type,
            stage="routing",
            decision="hard_fail",
            reasons=["wallet_distribution_high_risk"],
            signal_id=signal_id,
            ts_value=1_800_000_000,
        )
    monkeypatch.setattr(sls.time, "time", lambda: 1_800_000_100)

    summary = sls.get_historical_corpus_summary()

    assert summary["sampled"] is True
    assert summary["signals"]["sample_size"] == 1
    assert summary["signals"]["distinct_tokens_in_sample"] == 1
    assert summary["decisions"]["sample_size"] == 2
    assert summary["decisions"]["distinct_sample_signal_stage_decisions"] == 1
    assert summary["decisions"]["sample_repeated_decisions"] == 1
    assert summary["decisions"]["sample_repeat_rate"] == 50.0
    assert summary["decisions"]["top_reasons"][0] == {
        "reason": "wallet_distribution_high_risk",
        "count": 2,
    }
    assert summary["feature_coverage"]["attention_pct"] == 100.0
    assert summary["feature_coverage"]["market_cap_pct"] == 0.0


def test_duplicate_signal_decisions_are_suppressed_within_short_window(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_DECISION_DEDUP_WINDOW_SECONDS", "60")
    sls.init()

    event = Event(type="trade_buy", source="test", token="token-dedup", ts=1_800_000_000)
    signal_id = sls.record_signal_event(event)
    for offset in (0, 30):
        sls.record_signal_decision(
            token=event.token,
            event_type=event.type,
            stage="routing",
            decision="hard_fail",
            reasons=["wallet_top_holder_concentration"],
            signal_id=signal_id,
            ts_value=1_800_000_000 + offset,
        )

    with sls._connect() as c:
        count = c.execute("SELECT COUNT(1) FROM signal_decisions WHERE signal_id=?", (signal_id,)).fetchone()[0]

    assert count == 1


def test_duplicate_signal_decisions_are_recorded_after_dedup_window(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_DECISION_DEDUP_WINDOW_SECONDS", "60")
    sls.init()

    event = Event(type="trade_buy", source="test", token="token-dedup-later", ts=1_800_000_000)
    signal_id = sls.record_signal_event(event)
    for offset in (0, 61):
        sls.record_signal_decision(
            token=event.token,
            event_type=event.type,
            stage="routing",
            decision="hard_fail",
            reasons=["wallet_top_holder_concentration"],
            signal_id=signal_id,
            ts_value=1_800_000_000 + offset,
        )

    with sls._connect() as c:
        count = c.execute("SELECT COUNT(1) FROM signal_decisions WHERE signal_id=?", (signal_id,)).fetchone()[0]

    assert count == 2


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
    assert summary["counts_by_action"]["skip"] == 1
    assert summary["counts_by_action"]["block"] == 1
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


def test_classify_policy_regime_and_persist_on_decision(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    regime = sls.classify_policy_regime(
        {
            "session_bucket": "us_day",
            "liquidity_usd": 18000,
            "age_minutes": 22,
            "price_change_m5": 18.0,
            "price_change_h1": 55.0,
            "unique_buyers_15m": 44,
        },
        stage="candidate",
        ts_value=1_773_600_500,
    )
    signal_id = sls.record_signal_decision(
        token="token-regime",
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        features={
            "session_bucket": "us_day",
            "liquidity_usd": 18000,
            "age_minutes": 22,
            "price_change_m5": 18.0,
            "price_change_h1": 55.0,
            "unique_buyers_15m": 44,
        },
        attention_score=0.76,
        risk_score=0.20,
        confidence_score=0.70,
        creator_score=0.41,
        lifecycle="dex",
        ts_value=1_773_600_500,
        source="test",
    )

    with sls._connect() as c:
        row = c.execute(
            "SELECT features_json FROM signal_decisions WHERE signal_id=?",
            (signal_id,),
        ).fetchone()
    features = json.loads(row[0] or "{}")

    assert regime["regime_key"] == "candidate|us_day|mid|developing|building"
    assert features["regime_key"] == regime["regime_key"]
    assert features["session_regime"] == "us_day"
    assert features["liquidity_regime"] == "mid"


def test_regime_scoped_rollout_resolution_prefers_exact_match(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    sls.create_policy_profile(
        policy_name="global_candidate",
        policy_version="v1",
        config={"candidate_creator_min": 0.40},
        created_by="ops",
    )
    sls.create_policy_profile(
        policy_name="regime_candidate",
        policy_version="v2",
        config={"candidate_creator_min": 0.61},
        created_by="ops",
    )
    sls.activate_policy_rollout(
        policy_name="global_candidate",
        policy_version="v1",
        rollout_mode="active",
        stage_scope="candidate",
        priority=10,
        activated_by="ops",
    )
    rollout = sls.activate_policy_rollout(
        policy_name="regime_candidate",
        policy_version="v2",
        rollout_mode="active",
        stage_scope="candidate",
        regime_scope="candidate|us_day|mid|developing|building",
        priority=1,
        activated_by="ops",
    )

    exact = sls.resolve_live_policy(
        "candidate",
        token="token-123",
        regime_key="candidate|us_day|mid|developing|building",
    )
    fallback = sls.resolve_live_policy(
        "candidate",
        token="token-123",
        regime_key="candidate|asia|thin|new|flat",
    )
    rollouts = sls.list_policy_rollouts(limit=10, active_only=False)

    assert exact["policy_name"] == "regime_candidate"
    assert exact["regime_scope"] == "candidate|us_day|mid|developing|building"
    assert fallback["policy_name"] == "global_candidate"
    assert any(item["rollout_id"] == rollout["rollout_id"] and item["regime_scope"] for item in rollouts)


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
    assert all("proposal_score" in item for item in generated["generated"])
    assert all("proposal_rank" in item for item in generated["generated"])
    assert any(str(item["policy_name"]).startswith("generated_") for item in profiles)
    assert any(str(item.get("shadow_policy", {}).get("policy_name") or "").startswith("generated_") for item in replays)


def test_regime_scoped_generation_and_automation_preserve_regime_scope(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_PROMOTION_COOLDOWN_SEC", "0")
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-regime-promoted",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.72,
        risk_score=0.68,
        confidence_score=0.88,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "session_bucket": "us_day",
            "liquidity_usd": 9000.0,
            "age_minutes": 12.0,
            "price_change_m5": -18.0,
            "price_change_h1": -24.0,
            "unique_buyers_15m": 18,
        },
        ts_value=1_773_860_100,
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
                1_773_863_600,
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

    generated = sls.generate_policy_candidates(hours=10_000, generation_limit=6, replay_limit=200)
    regime_generated = next(
        item for item in generated["generated"] if str(item.get("regime_key") or "").startswith("promoted|us_day|thin|new|reversing")
    )
    replay = regime_generated["replay"]

    approval = sls.create_policy_approval(
        policy_name=regime_generated["profile"]["policy_name"],
        policy_version=regime_generated["profile"]["policy_version"],
        source_type="replay",
        source_ref=replay["run_id"],
        approved_by="ops",
    )
    scheduled = sls.auto_schedule_policy_canaries(hours=10_000)
    canary = next(
        item for item in scheduled["scheduled"]
        if item["policy_name"] == regime_generated["profile"]["policy_name"]
        and item["policy_version"] == regime_generated["profile"]["policy_version"]
    )

    for idx in range(3):
        canary_signal_id = sls.record_signal_decision(
            token=f"token-regime-canary-{idx}",
            event_type="promoted",
            stage="promoted",
            decision="promoted_sent",
            action_taken="emit",
            attention_score=0.84,
            risk_score=0.20,
            confidence_score=0.93,
            creator_score=0.32,
            lifecycle="dex",
            policy_name=regime_generated["profile"]["policy_name"],
            policy_version=regime_generated["profile"]["policy_version"],
            features={
                "session_bucket": "us_day",
                "liquidity_usd": 9000.0,
                "age_minutes": 10.0,
                "price_change_m5": -12.0,
                "price_change_h1": -22.0,
                "unique_buyers_15m": 16,
            },
            ts_value=1_773_870_000 + idx,
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
                    1_773_873_600 + idx,
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

    promoted = sls.auto_promote_policy_canaries(hours=10_000)
    active = next(
        item["active_rollout"]
        for item in promoted["promoted"]
        if item["active_rollout"]["policy_name"] == regime_generated["profile"]["policy_name"]
    )

    assert approval["approval_status"] == "approved"
    assert replay["regime_key"] == "promoted|us_day|thin|new|reversing"
    assert canary["regime_scope"] == replay["regime_key"]
    assert active["regime_scope"] == replay["regime_key"]


def test_policy_automation_cycle_executes_auto_regime_actions(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_PROMOTION_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_REGIME_ACTION_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_MAX_REGIME_ACTIONS_PER_DAY", "1")
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-auto-regime",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.72,
        risk_score=0.68,
        confidence_score=0.88,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "session_bucket": "us_day",
            "liquidity_usd": 9000.0,
            "age_minutes": 12.0,
            "price_change_m5": -18.0,
            "price_change_h1": -24.0,
            "unique_buyers_15m": 18,
        },
        ts_value=1_773_960_100,
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
                1_773_963_600,
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

    cycle = sls.run_policy_automation_cycle(hours=10_000, replay_limit=100)
    events = sls.list_policy_rollout_events(limit=50)

    assert cycle["regime_actions"]["executed"]
    assert any(item["event_type"] == "auto_regime_action_started" for item in events)
    assert cycle["generated"]["generated"]


def test_regime_action_feedback_scores_outcomes_and_recommends_action(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_PROMOTION_COOLDOWN_SEC", "0")
    sls.init()

    sls.record_signal_decision(
        token="token-feedback-seed",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.72,
        risk_score=0.68,
        confidence_score=0.88,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "session_bucket": "us_day",
            "liquidity_usd": 9000.0,
            "age_minutes": 12.0,
            "price_change_m5": -18.0,
            "price_change_h1": -24.0,
            "unique_buyers_15m": 18,
        },
        ts_value=1_773_990_100,
        source="test",
    )
    result = sls.execute_regime_policy_action(
        regime_key="promoted|us_day|thin|new|reversing",
        action="canary_tighten",
        actor="ops-user",
        hours=10000,
        replay_limit=100,
    )
    for idx in range(3):
        signal_id = sls.record_signal_decision(
            token=f"token-feedback-{idx}",
            event_type="promoted",
            stage="promoted",
            decision="promoted_sent",
            action_taken="emit",
            attention_score=0.84,
            risk_score=0.20,
            confidence_score=0.93,
            creator_score=0.32,
            lifecycle="dex",
            policy_name=result["profile"]["policy_name"],
            policy_version=result["profile"]["policy_version"],
            features={
                "session_bucket": "us_day",
                "liquidity_usd": 9000.0,
                "age_minutes": 10.0,
                "price_change_m5": -12.0,
                "price_change_h1": -22.0,
                "unique_buyers_15m": 16,
            },
            ts_value=1_774_000_000 + idx,
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
                    1_774_003_600 + idx,
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
    sls.auto_promote_policy_canaries(hours=10000)
    feedback = sls.get_regime_action_feedback(hours=10000)
    regime_feedback = feedback["by_regime"]["promoted|us_day|thin|new|reversing"]

    assert feedback["evaluations"]
    assert regime_feedback["actions"]["canary_tighten"]["correct"] >= 1
    assert regime_feedback["recommended_action"] == "canary_tighten"


def test_regime_meta_policy_adapts_confidence_and_traffic(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_TRAFFIC_PERCENT", "10")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_PROMOTION_COOLDOWN_SEC", "0")
    sls.init()

    sls.record_signal_decision(
        token="token-meta-seed",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.72,
        risk_score=0.68,
        confidence_score=0.88,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "session_bucket": "us_day",
            "liquidity_usd": 9000.0,
            "age_minutes": 12.0,
            "price_change_m5": -18.0,
            "price_change_h1": -24.0,
            "unique_buyers_15m": 18,
        },
        ts_value=1_774_010_100,
        source="test",
    )
    result = sls.execute_regime_policy_action(
        regime_key="promoted|us_day|thin|new|reversing",
        action="canary_tighten",
        actor="ops-user",
        hours=10000,
        replay_limit=100,
        traffic_percent=20,
    )
    for idx in range(3):
        signal_id = sls.record_signal_decision(
            token=f"token-meta-{idx}",
            event_type="promoted",
            stage="promoted",
            decision="promoted_sent",
            action_taken="emit",
            attention_score=0.84,
            risk_score=0.20,
            confidence_score=0.93,
            creator_score=0.32,
            lifecycle="dex",
            policy_name=result["profile"]["policy_name"],
            policy_version=result["profile"]["policy_version"],
            features={
                "session_bucket": "us_day",
                "liquidity_usd": 9000.0,
                "age_minutes": 10.0,
                "price_change_m5": -12.0,
                "price_change_h1": -22.0,
                "unique_buyers_15m": 16,
            },
            ts_value=1_774_020_000 + idx,
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
                    1_774_023_600 + idx,
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
    sls.auto_promote_policy_canaries(hours=10000)
    meta = sls.get_regime_meta_policy(hours=10000)
    regime_meta = meta["by_regime"]["promoted|us_day|thin|new|reversing"]

    assert regime_meta["confidence_score"] >= 55.0
    assert regime_meta["recommended_action"] == "canary_tighten"
    assert regime_meta["traffic_percent"] >= 10


def test_auto_execute_regime_actions_uses_meta_policy_traffic(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_TRAFFIC_PERCENT", "10")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_REGIME_ACTION_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_MAX_REGIME_ACTIONS_PER_DAY", "1")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_PROMOTION_COOLDOWN_SEC", "0")
    sls.init()

    seed_id = sls.record_signal_decision(
        token="token-auto-meta-seed",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.72,
        risk_score=0.68,
        confidence_score=0.88,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "session_bucket": "us_day",
            "liquidity_usd": 9000.0,
            "age_minutes": 12.0,
            "price_change_m5": -18.0,
            "price_change_h1": -24.0,
            "unique_buyers_15m": 18,
        },
        ts_value=1_774_030_100,
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
                seed_id,
                60,
                1_774_033_600,
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
    prior = sls.execute_regime_policy_action(
        regime_key="promoted|us_day|thin|new|reversing",
        action="canary_tighten",
        actor="ops-user",
        hours=10000,
        replay_limit=100,
        traffic_percent=20,
    )
    for idx in range(3):
        signal_id = sls.record_signal_decision(
            token=f"token-auto-meta-{idx}",
            event_type="promoted",
            stage="promoted",
            decision="promoted_sent",
            action_taken="emit",
            attention_score=0.84,
            risk_score=0.20,
            confidence_score=0.93,
            creator_score=0.32,
            lifecycle="dex",
            policy_name=prior["profile"]["policy_name"],
            policy_version=prior["profile"]["policy_version"],
            features={
                "session_bucket": "us_day",
                "liquidity_usd": 9000.0,
                "age_minutes": 10.0,
                "price_change_m5": -12.0,
                "price_change_h1": -22.0,
                "unique_buyers_15m": 16,
            },
            ts_value=1_774_040_000 + idx,
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
                    1_774_043_600 + idx,
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
    sls.auto_promote_policy_canaries(hours=10000)
    auto_result = sls.auto_execute_regime_actions(hours=10000, replay_limit=100)

    assert auto_result["meta_policy"]["by_regime"]["promoted|us_day|thin|new|reversing"]["traffic_percent"] >= 10
    assert auto_result["executed"]
    assert int(auto_result["executed"][0]["traffic_percent"]) >= 10


def test_auto_execute_regime_actions_respects_portfolio_traffic_budget(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_TRAFFIC_PERCENT", "10")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_REGIME_ACTION_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_MAX_REGIME_ACTIONS_PER_DAY", "2")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_MAX_TOTAL_CANARY_TRAFFIC_PERCENT", "12")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_CANARY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SIGNAL_ENGINE_POLICY_PROMOTION_COOLDOWN_SEC", "0")
    sls.init()

    first_seed = sls.record_signal_decision(
        token="token-portfolio-one",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.72,
        risk_score=0.68,
        confidence_score=0.88,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "session_bucket": "us_day",
            "liquidity_usd": 9000.0,
            "age_minutes": 12.0,
            "price_change_m5": -18.0,
            "price_change_h1": -24.0,
            "unique_buyers_15m": 18,
        },
        ts_value=1_774_050_100,
        source="test",
    )
    second_seed = sls.record_signal_decision(
        token="token-portfolio-two",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.80,
        risk_score=0.70,
        confidence_score=0.89,
        creator_score=0.35,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="deterministic-v1",
        features={
            "session_bucket": "asia",
            "liquidity_usd": 8500.0,
            "age_minutes": 14.0,
            "price_change_m5": -22.0,
            "price_change_h1": -28.0,
            "unique_buyers_15m": 14,
        },
        ts_value=1_774_050_200,
        source="test",
    )
    with sls._connect() as c:
        for signal_id, captured_ts in ((first_seed, 1_774_053_600), (second_seed, 1_774_053_700)):
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
                    captured_ts,
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
    sls.execute_regime_policy_action(
        regime_key="promoted|us_day|thin|new|reversing",
        action="canary_tighten",
        actor="ops-user",
        hours=10000,
        replay_limit=100,
        traffic_percent=10,
    )
    auto_result = sls.auto_execute_regime_actions(hours=10000, replay_limit=100)

    assert any(item["reason"] == "portfolio_traffic_exhausted" for item in auto_result["skipped"])


def test_policy_strategy_synthesis_scores_and_ranks_candidates(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    worked_signal_id = sls.record_signal_decision(
        token="token-synth-worked",
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
            "session_bucket": "us_day",
            "attention_score": 0.19,
            "creator_score": 0.22,
            "candidate_rate_limit_allowed": True,
            "candidate_progression_ok": True,
            "candidate_send_eligible": True,
        },
        ts_value=1_774_060_000,
        source="test",
    )
    failed_signal_id = sls.record_signal_decision(
        token="token-synth-failed",
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
            "session_bucket": "us_day",
            "liquidity_usd": 9000.0,
            "age_minutes": 12.0,
            "price_change_m5": -18.0,
            "price_change_h1": -24.0,
            "unique_buyers_15m": 18,
        },
        ts_value=1_774_060_100,
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
                1_774_063_600,
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
                1_774_063_700,
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

    synthesis = sls.get_policy_strategy_synthesis(hours=10000)
    generated = sls.generate_policy_candidates(hours=10000, generation_limit=6, replay_limit=200)

    assert synthesis["by_regime"]
    assert generated["strategy"]["by_regime"]
    assert generated["generated"]
    assert float(generated["generated"][0]["proposal_score"]) >= float(generated["generated"][-1]["proposal_score"])
    assert generated["generated"][0]["proposal_rank"] == 1


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
            ("sig-a3", "token-a3", "candidate", "asia", "pending"),
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
    assert combos[("asia", "candidate")]["total"] == 3
    assert combos[("asia", "candidate")]["resolved_total"] == 2
    assert combos[("us_day", "promoted")]["fail_rate"] == 100.0
    assert summary["outcome_quality"]["resolved_total"] == 4
    assert summary["outcome_quality"]["win_rate"] == 50.0
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


def test_record_signal_decision_uses_remote_write_for_worker_when_configured(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_PROCESS_ROLE", "worker")
    monkeypatch.delenv("SIGNAL_ENGINE_DB_PATH", raising=False)
    monkeypatch.delenv("STATE_ENGINE_DB_PATH", raising=False)
    monkeypatch.setenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "https://engine.example.com")
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_remote(endpoint: str, payload: dict[str, object]):
        calls.append((endpoint, payload))
        return {"signal_id": "remote-signal-1"}

    monkeypatch.setattr(sls, "_post_internal_learning_write", fake_remote)

    signal_id = sls.record_signal_decision(
        token="token-remote",
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        reasons=["remote"],
        attention_score=0.7,
        risk_score=0.2,
        confidence_score=0.8,
        lifecycle="dex",
    )

    assert signal_id == "remote-signal-1"
    assert calls
    assert calls[0][0] == "/learning/internal/decisions"


def test_worker_auto_write_mode_prefers_remote_even_with_local_db_path(monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_PROCESS_ROLE", "worker")
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", "/var/data/engine.db")
    monkeypatch.setenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "https://engine.example.com")
    monkeypatch.delenv("SIGNAL_ENGINE_LEARNING_WRITE_MODE", raising=False)

    config = sls._learning_write_config()

    assert config["mode"] == "remote"
    assert config["remote_enabled"] is True


def test_internal_learning_write_retries_transient_gateway_failure(monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "https://engine.example.com")
    responses = [
        httpx.Response(502, request=httpx.Request("POST", "https://engine.example.com")),
        httpx.Response(
            200,
            request=httpx.Request("POST", "https://engine.example.com"),
            json={"recorded": True},
        ),
    ]
    sleeps: list[float] = []

    monkeypatch.setattr(sls.httpx, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(sls.time, "sleep", sleeps.append)

    result = sls._post_internal_learning_write(
        "/learning/internal/heartbeat",
        {"service_role": "worker"},
    )

    assert result == {"recorded": True}
    assert sleeps == [0.5]


def test_internal_learning_write_does_not_retry_auth_failure(monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "https://engine.example.com")
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            403,
            request=httpx.Request("POST", "https://engine.example.com"),
        )

    monkeypatch.setattr(sls.httpx, "post", forbidden)
    monkeypatch.setattr(
        sls.time,
        "sleep",
        lambda delay: pytest.fail(f"unexpected retry delay: {delay}"),
    )

    assert (
        sls._post_internal_learning_write(
            "/learning/internal/heartbeat",
            {"service_role": "worker"},
        )
        is None
    )
    assert calls == 1


def test_ingest_signal_event_and_decision_persist_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_payload = {
        "event": {
            "type": "candidate",
            "source": "test",
            "token": "token-ingest",
            "creator": "creator-a",
            "confidence": 0.63,
            "reasons": ["ingested"],
            "ts": 1_773_800_000,
            "extra": {
                "lifecycle": "dex",
                "attention_score": 0.61,
                "risk_score": 0.22,
                "dex_summary": {
                    "liquidity_usd": 9000,
                    "volume_m5": 3000,
                },
            },
        },
        "external_ref": "msg-200",
    }
    signal_result = sls.ingest_signal_event(signal_payload)
    decision_result = sls.ingest_signal_decision(
        {
            "token": "token-ingest",
            "event_type": "candidate",
            "stage": "candidate",
            "decision": "candidate_ready",
            "action_taken": "emit",
            "reasons": ["ingested"],
            "features": {"attention_score": 0.61},
            "confidence_score": 0.63,
            "attention_score": 0.61,
            "risk_score": 0.22,
            "lifecycle": "dex",
            "signal_id": signal_result["signal_id"],
            "source": "test",
        }
    )

    with sls._connect() as c:
        signal_count = c.execute("SELECT COUNT(1) FROM signals WHERE token='token-ingest'").fetchone()[0]
        decision_count = c.execute("SELECT COUNT(1) FROM signal_decisions WHERE token='token-ingest'").fetchone()[0]

    assert signal_result["signal_id"]
    assert decision_result["signal_id"] == signal_result["signal_id"]
    assert signal_count == 1
    assert decision_count == 1


def test_live_validation_summary_tracks_alert_outcomes_and_buckets(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    candidate = Event(
        type="candidate",
        source="engine",
        token="token-live-candidate",
        confidence=0.62,
        ts=1_773_900_000,
        extra={"lifecycle": "dex", "attention_score": 0.58, "risk_score": 0.18},
    )
    candidate_id = sls.record_signal_event(candidate, external_ref="msg-live-1")
    sls.record_signal_decision(
        token=candidate.token,
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        signal_id=candidate_id,
        attention_score=0.58,
        risk_score=0.18,
        confidence_score=0.62,
        lifecycle="dex",
        policy_name="live_policy",
        policy_version="live-v1",
        features={
            "route_tier": "candidate",
            "candidate_send_eligible": True,
            "tracked_wallet_hits": 1,
        },
        ts_value=1_773_900_000,
    )

    skipped_id = sls.record_signal_decision(
        token="token-live-missed",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        action_taken="hold",
        reasons=["attention<0.20", "buyers_low"],
        attention_score=0.19,
        risk_score=0.16,
        confidence_score=0.44,
        lifecycle="dex",
        policy_name="live_policy",
        policy_version="live-v1",
        features={"route_tier": "sniper", "sniper_blockers": ["buyers_low"]},
        ts_value=1_773_900_030,
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
                candidate_id,
                60,
                1_773_903_600,
                "dex",
                22000,
                7000,
                6000,
                65.0,
                18.0,
                48.0,
                90,
                30,
                35.0,
                10.0,
                20.0,
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
                skipped_id,
                60,
                1_773_903_900,
                "dex",
                48000,
                12000,
                9500,
                62.0,
                40.0,
                95.0,
                180,
                55,
                140.0,
                30.0,
                60.0,
                "strong_continuation",
                json.dumps({"outcome_label": "strong_continuation"}),
            ),
        )

    summary = sls.get_live_validation_summary(hours=10_000, limit=50)

    assert summary["sent_alerts"] == 1
    assert summary["route_counts"]["candidate"] >= 1
    assert (
        summary["evaluation_buckets"].get("decent_signal", 0)
        + summary["evaluation_buckets"].get("too_early_but_valid", 0)
    ) >= 1
    assert summary["missed_runner_analysis"]["missed_runner_count"] >= 1
    assert summary["missed_runner_analysis"]["missed_runners"][0]["miss_bucket"] == "missed_sniper"
    assert summary["policy_comparison"]["variant_count"] >= 1
    assert summary["alerts"][0]["thresholds_used"]["policy_name"] == "live_policy"


def test_live_validation_summary_uses_bounded_policy_comparison(monkeypatch):
    captured: dict[str, int] = {}

    monkeypatch.setattr(sls, "get_live_validation_records", lambda **kwargs: [])
    monkeypatch.setattr(sls, "get_diagnostics_summary", lambda **kwargs: {"threshold_guidance": [], "top_skip_reasons": []})

    def fake_policy_comparison(**kwargs):
        captured.update(kwargs)
        return {"lookback_hours": kwargs["hours"], "variant_count": 0, "variants": []}

    monkeypatch.setattr(sls, "get_policy_validation_comparison", fake_policy_comparison)

    summary = sls.get_live_validation_summary(hours=72, limit=10_000)

    assert captured["record_limit"] == 1000
    assert summary["total_tracked_opportunities"] == 0
    assert summary["missed_runner_analysis"]["missed_runner_count"] == 0


def test_live_validation_records_mark_unsent_decision_shells(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-shell-only",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.12,
        risk_score=0.20,
        confidence_score=0.28,
        lifecycle="dex",
        policy_name="live_policy",
        policy_version="live-v2",
        ts_value=1_773_910_000,
    )

    records = sls.get_live_validation_records(hours=10_000, limit=20)
    record = next(item for item in records if item["signal_id"] == signal_id)

    assert record["sent_to_discord"] is False
    assert record["final_route_class"] == "reject"
    assert record["thresholds_used"]["policy_version"] == "live-v2"
    assert "parameter_fingerprint" in record


def test_observe_review_lifecycle_sync_recheck_and_action(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setattr(sls.time, "time", lambda: 1_773_920_000)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-observe-life",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        action_taken="hold",
        reasons=["wallet_top_holder_concentration"],
        attention_score=0.22,
        risk_score=0.18,
        confidence_score=0.48,
        lifecycle="dex",
        features={
            "route_tier": "candidate",
            "wallet_guard_category": "concentration_watch",
            "wallet_guard_watch_only": True,
            "market_cap_usd": 21000,
            "liquidity_usd": 16000,
            "unique_buyers_5m": 5,
        },
        ts_value=1_773_920_000,
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
                1_773_923_600,
                "dex",
                33000,
                17000,
                8000,
                60.0,
                6.0,
                32.0,
                22,
                10,
                55.0,
                5.0,
                10.0,
                "worked",
                json.dumps(
                    {
                        "outcome_label": "worked",
                        "liquidity_usd": 17000,
                        "price_change_m5": 6.0,
                        "txns_m5_buys": 22,
                        "txns_m5_sells": 10,
                    }
                ),
            ),
        )

    monkeypatch.setattr(sls.time, "time", lambda: 1_773_924_000)
    sync = sls.sync_observe_review_queue(hours=10_000, limit=20)
    lifecycle = sls.get_observe_lifecycle_state(limit=10)

    assert sync["created"] == 1
    assert lifecycle["items"][0]["token"] == "token-observe-life"
    assert lifecycle["items"][0]["status"] == "ready_for_watch"
    assert lifecycle["items"][0]["observe_shadow"]["latest_market_cap_change_pct"] == 55.0

    with sls._connect() as c:
        c.execute("UPDATE observe_reviews SET next_recheck_ts=? WHERE token=?", (1_773_919_999, "token-observe-life"))
    recheck = sls.run_observe_rechecks(limit=5)

    assert recheck["processed"] == 1
    assert recheck["items"][0]["status"] == "ready_for_watch"

    action = sls.apply_observe_review_action(
        "token-observe-life",
        action="track_shadow_only",
        note="keep observing manually",
        operator="pytest",
    )

    assert action["status"] == "shadow_only"
    assert sls.get_observe_lifecycle_state(limit=10)["items"][0]["operator_note"] == "keep observing manually"


def test_watch_override_approval_requires_current_market_floor_and_revokes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setattr(sls.time, "time", lambda: 1_773_950_000)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-watch-override",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        action_taken="hold",
        reasons=["wallet_top_holder_concentration"],
        attention_score=0.31,
        risk_score=0.16,
        confidence_score=0.52,
        lifecycle="dex",
        features={
            "wallet_guard_category": "concentration_watch",
            "wallet_guard_watch_only": True,
            "wallet_guard_original_reasons": ["wallet_top_holder_concentration"],
            "market_cap_usd": 32000,
            "liquidity_usd": 18000,
        },
        ts_value=1_773_950_000,
    )
    with sls._connect() as c:
        c.execute(
            """
            INSERT INTO observe_reviews (
                token, first_signal_id, latest_signal_id, status, action, priority, graduation_score,
                graduation_stage, wallet_category, reasons_json, payload_json, first_seen_ts,
                updated_ts, next_recheck_ts, recheck_count
            ) VALUES (?, ?, ?, 'ready_for_watch', 'watch_now', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                "token-watch-override",
                signal_id,
                signal_id,
                10.0,
                94.0,
                "graduated",
                "concentration_watch",
                json.dumps(["wallet_top_holder_concentration"]),
                json.dumps({"market_cap_change_pct": 140.0}),
                1_773_950_000,
                1_773_950_000,
                0,
            ),
        )

    action = sls.apply_observe_review_action(
        "token-watch-override",
        action="approve_watch_override",
        note="pytest approve",
        operator="pytest",
    )
    active = sls.get_active_watch_override("token-watch-override")
    below_floor = sls.resolve_watch_override_for_worker(
        "token-watch-override",
        market_cap_usd=99_000,
        liquidity_usd=20_000,
    )
    consumable = sls.resolve_watch_override_for_worker(
        "token-watch-override",
        market_cap_usd=110_000,
        liquidity_usd=20_000,
    )

    assert action["watch_override"]["status"] == "active"
    assert active is not None
    assert active["target_market_cap_usd"] == 100000.0
    assert below_floor["consumable"] is False
    assert below_floor["checks"]["market_cap_ok"] is False
    assert consumable["consumable"] is True

    revoked = sls.apply_observe_review_action(
        "token-watch-override",
        action="mark_bad",
        note="pytest revoke",
        operator="pytest",
    )

    assert revoked["watch_override"]["status"] == "revoked"
    assert sls.get_active_watch_override("token-watch-override") is None


def test_watch_override_autopilot_fills_bounded_top_slots(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setattr(sls.time, "time", lambda: 1_773_960_000)
    sls.init()

    rows = [
        ("token-auto-1", 120.0, 94.0, 150.0),
        ("token-auto-2", 112.0, 93.0, 108.0),
        ("token-auto-low", 130.0, 89.0, 180.0),
    ]
    prepared_rows = []
    for token, priority, score, mc_change in rows:
        signal_id = sls.record_signal_decision(
            token=token,
            event_type="candidate",
            stage="candidate",
            decision="candidate_gate_skip",
            action_taken="hold",
            reasons=["wallet_top_holder_concentration"],
            features={"wallet_guard_category": "concentration_watch", "wallet_guard_watch_only": True},
            ts_value=1_773_960_000,
        )
        prepared_rows.append((token, signal_id, priority, score, mc_change))

    with sls._connect() as c:
        for token, signal_id, priority, score, mc_change in prepared_rows:
            c.execute(
                """
                INSERT INTO observe_reviews (
                    token, first_signal_id, latest_signal_id, status, action, priority, graduation_score,
                    graduation_stage, wallet_category, reasons_json, payload_json, first_seen_ts,
                    updated_ts, next_recheck_ts, recheck_count
                ) VALUES (?, ?, ?, 'ready_for_watch', 'watch_now', ?, ?, 'graduated', 'concentration_watch', ?, ?, ?, ?, NULL, 0)
                """,
                (
                    token,
                    signal_id,
                    signal_id,
                    priority,
                    score,
                    json.dumps(["wallet_top_holder_concentration"]),
                    json.dumps({"market_cap_change_pct": mc_change}),
                    1_773_960_000,
                    1_773_960_000,
                ),
            )

    run = sls.run_watch_override_autopilot(limit=5, max_active=2, operator="pytest")
    active = sls.get_watch_overrides(status="active", limit=10)["items"]
    second_run = sls.run_watch_override_autopilot(limit=5, max_active=2, operator="pytest")
    status = sls.get_watch_override_autopilot_status(ready_limit=10)

    assert run["status"] == "activated"
    assert run["activated_count"] == 2
    assert {item["token"] for item in active} == {"token-auto-1", "token-auto-2"}
    assert second_run["status"] == "full"
    low_row = next(item for item in status["ready"] if item["token"] == "token-auto-low")
    assert "graduation_score_below_autopilot_floor" in low_row["autopilot_blockers"]


def test_wallet_guard_feedback_groups_watch_only_outcomes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-wallet-feedback",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        action_taken="hold",
        reasons=["wallet_top_holder_concentration"],
        attention_score=0.24,
        risk_score=0.19,
        confidence_score=0.50,
        lifecycle="dex",
        features={
            "wallet_guard_category": "concentration_watch",
            "wallet_guard_watch_only": True,
            "wallet_guard_original_reasons": ["wallet_top_holder_concentration"],
        },
        ts_value=1_773_930_000,
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
                1_773_933_600,
                "dex",
                42000,
                18000,
                9000,
                62.0,
                10.0,
                40.0,
                18,
                9,
                80.0,
                12.0,
                20.0,
                "strong_continuation",
                json.dumps({"outcome_label": "strong_continuation"}),
            ),
        )

    feedback = sls.get_wallet_guard_feedback(hours=10_000, limit=50)
    group = next(item for item in feedback["categories"] if item["category"] == "concentration_watch")

    assert group["watch_only"] == 1
    assert group["positive_unsent"] == 1
    assert group["recommendation"] in {"graduate_more_with_review", "keep_observing"}


def test_wallet_guard_category_infers_from_legacy_reasons(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setattr(sls.time, "time", lambda: 1_773_940_000)
    sls.init()

    signal_id = sls.record_signal_decision(
        token="token-infer-wallet",
        event_type="candidate",
        stage="candidate",
        decision="hard_fail",
        action_taken="hold",
        reasons=["wallet_distribution_high_risk", "wallet_top_holder_concentration"],
        attention_score=0.18,
        risk_score=0.50,
        confidence_score=0.30,
        lifecycle="dex",
        features={"route_tier": "candidate"},
        ts_value=1_773_940_000,
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
                1_773_943_600,
                "dex",
                50000,
                22000,
                11000,
                65.0,
                12.0,
                38.0,
                24,
                8,
                70.0,
                15.0,
                25.0,
                "worked",
                json.dumps(
                    {
                        "outcome_label": "worked",
                        "liquidity_usd": 22000,
                        "price_change_m5": 12.0,
                        "txns_m5_buys": 24,
                        "txns_m5_sells": 8,
                    }
                ),
            ),
        )

    record = next(item for item in sls.get_live_validation_records(hours=10_000, limit=20) if item["signal_id"] == signal_id)
    assert record["wallet_guard"]["category"] == "concentration_watch"
    assert record["wallet_guard"]["watch_only"] is True

    sync = sls.sync_observe_review_queue(hours=10_000, limit=20)
    with sls._connect() as c:
        c.execute(
            "UPDATE observe_reviews SET wallet_category='none', payload_json=? WHERE token=?",
            (
                json.dumps(
                    {
                        "binding_reasons": ["wallet_distribution_high_risk", "wallet_top_holder_concentration"],
                        "wallet_guard": {"category": "none", "watch_only": False},
                        "market_cap_change_pct": 70.0,
                    }
                ),
                "token-infer-wallet",
            ),
        )
    ready = sls.get_ready_for_watch_queue(limit=10)

    assert sync["created"] == 1
    assert ready["items"][0]["token"] == "token-infer-wallet"
    assert ready["items"][0]["wallet_category"] == "concentration_watch"


def test_policy_validation_comparison_ranks_variants_by_live_quality(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    good_event = Event(
        type="heating_up",
        source="engine",
        token="token-good-variant",
        confidence=0.84,
        ts=1_773_920_000,
        extra={"lifecycle": "dex", "attention_score": 0.82, "risk_score": 0.14},
    )
    good_id = sls.record_signal_event(good_event)
    sls.record_signal_decision(
        token=good_event.token,
        event_type="heating_up",
        stage="heating_up",
        decision="heating_sent",
        action_taken="emit",
        signal_id=good_id,
        attention_score=0.82,
        risk_score=0.14,
        confidence_score=0.84,
        lifecycle="dex",
        policy_name="policy_a",
        policy_version="v1",
        features={"route_tier": "sniper", "sniper_ready": True},
        ts_value=1_773_920_000,
    )

    bad_event = Event(
        type="promoted",
        source="engine",
        token="token-bad-variant",
        confidence=0.71,
        ts=1_773_920_100,
        extra={"lifecycle": "dex", "attention_score": 0.65, "risk_score": 0.41},
    )
    bad_id = sls.record_signal_event(bad_event)
    sls.record_signal_decision(
        token=bad_event.token,
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        signal_id=bad_id,
        attention_score=0.65,
        risk_score=0.41,
        confidence_score=0.71,
        lifecycle="dex",
        policy_name="policy_b",
        policy_version="v9",
        features={"route_tier": "promoted"},
        ts_value=1_773_920_100,
    )

    with sls._connect() as c:
        for signal_id, outcome_label, mc_change in ((good_id, "strong_continuation", 120.0), (bad_id, "failed", -35.0)):
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
                    1_773_923_600,
                    "dex",
                    30000,
                    8000,
                    7000,
                    70.0,
                    10.0,
                    45.0,
                    100,
                    30,
                    mc_change,
                    12.0,
                    18.0,
                    outcome_label,
                    json.dumps({"outcome_label": outcome_label}),
                ),
            )

    comparison = sls.get_policy_validation_comparison(hours=10_000, limit=10)

    assert comparison["variant_count"] >= 2
    assert comparison["variants"][0]["policy_key"] == "policy_a@v1"
    assert comparison["variants"][0]["precision"] >= comparison["variants"][1]["precision"]

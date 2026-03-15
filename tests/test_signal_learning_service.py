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

    report = sls.generate_daily_learning_report(report_date)

    assert report["report_date"] == report_date
    assert report["totals_by_type"]["promoted"] == 1
    assert report["outcomes_by_label"]["strong_continuation"] == 1
    assert report["sessions"]

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

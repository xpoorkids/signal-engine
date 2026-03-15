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

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

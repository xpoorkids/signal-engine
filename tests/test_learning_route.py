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


def test_learning_report_route_404_when_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    client = TestClient(main.app)
    response = client.get("/learning/report/latest")

    assert response.status_code == 404


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

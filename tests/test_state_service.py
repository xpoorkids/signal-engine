from __future__ import annotations

from app.services import state_service


def test_candidate_send_state_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(state_service, "DB_PATH", db_path)
    state_service.DB_PATH.parent.mkdir(exist_ok=True)
    state_service.init()

    state_service.record_candidate_sent("token-1")
    send_state = state_service.get_candidate_send_state("token-1")

    assert send_state["candidate_sent_count"] == 1
    assert send_state["candidate_last_sent"] > 0
    assert send_state["last_metrics"] == {}


def test_state_service_uses_hardened_sqlite_connection(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(state_service, "DB_PATH", db_path)
    state_service.DB_PATH.parent.mkdir(exist_ok=True)
    state_service.init()

    with state_service._connect() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) >= 5000


def test_candidate_rate_limit_only_consumes_when_recorded(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(state_service, "DB_PATH", db_path)
    state_service.DB_PATH.parent.mkdir(exist_ok=True)
    state_service.init()

    assert state_service.allow_candidate_rate_limit(1) is True
    assert state_service.allow_candidate_rate_limit(1) is True

    assert state_service.consume_candidate_rate_limit(1) is True
    assert state_service.allow_candidate_rate_limit(1) is False
    assert state_service.consume_candidate_rate_limit(1) is False

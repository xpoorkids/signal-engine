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


def test_candidate_rate_limit_recovers_future_window(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(state_service, "DB_PATH", db_path)
    state_service.DB_PATH.parent.mkdir(exist_ok=True)
    state_service.init()

    now = 1_800_000_000
    monkeypatch.setattr(state_service.time, "time", lambda: now)
    state_service.kv_set("candidate_rate_v2_window_start", str(now + 7200))
    state_service.kv_set("candidate_rate_v2_window_count", "5")

    state = state_service.get_candidate_rate_limit_state(5)

    assert state["allowed"] is True
    assert state["normalized"] is True
    assert state["window_count"] == 0
    assert state["remaining"] == 5

    assert state_service.consume_candidate_rate_limit(5) is True

    persisted = state_service.get_candidate_rate_limit_state(5)
    assert persisted["raw_window_start"] == now
    assert persisted["raw_window_count"] == 1
    assert persisted["remaining"] == 4

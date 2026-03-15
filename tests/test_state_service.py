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

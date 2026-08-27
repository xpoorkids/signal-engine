from __future__ import annotations

from research.config import load_config
from research.features.snapshots import build_fixture_snapshots
from research.replay.action_replay import run_fixture_action_replay
from research.storage import ResearchStore


def test_action_replay_is_deterministic_and_reuses_policy(tmp_path) -> None:
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    build_fixture_snapshots(config, winners=1, controls_per_winner=1)
    first = run_fixture_action_replay(config)
    second = run_fixture_action_replay(config)
    assert first["replays"] == second["replays"] == 18
    with ResearchStore(config).connect() as conn:
        row = conn.execute("SELECT summary_json FROM research_action_replays LIMIT 1").fetchone()
    assert "app.services.action_engine_service.ActionEngineService" in row["summary_json"]


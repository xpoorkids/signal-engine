from __future__ import annotations

from research.config import load_config
from research.features.snapshots import build_fixture_snapshots
from research.outcomes.labels import build_fixture_outcomes, excursion_metrics, target_before_stop
from research.storage import ResearchStore


def test_target_before_stop_is_chronological() -> None:
    path = [{"ts": 1, "price": 1.0}, {"ts": 2, "price": 0.8}, {"ts": 3, "price": 1.5}]
    assert target_before_stop(path, entry_ts=1, target_pct=25.0, stop_pct=-18.0) == "stop_before_target"


def test_excursion_metrics() -> None:
    metrics = excursion_metrics([{"price": 1.0}, {"price": 1.5}, {"price": 0.75}])
    assert metrics["maximum_favorable_excursion_pct"] == 50.0
    assert metrics["maximum_adverse_excursion_pct"] == -25.0


def test_fixture_outcomes_persist(tmp_path) -> None:
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    build_fixture_snapshots(config)
    result = build_fixture_outcomes(config)
    assert result["outcomes"] == 72
    with ResearchStore(config).connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM research_outcomes").fetchone()["c"] == 72


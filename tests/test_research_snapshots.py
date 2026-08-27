from __future__ import annotations

from research.config import load_config
from research.features.snapshots import build_fixture_snapshots, build_snapshot_features
from research.storage import ResearchStore


def test_snapshot_excludes_future_rows_and_missing_is_not_zero() -> None:
    features = build_snapshot_features(
        [{"ts": 10, "price": 1.0, "liquidity": 100.0, "side": "buy"}, {"ts": 20, "price": 2.0, "liquidity": 200.0, "side": "buy"}],
        10,
    )
    assert features["price"]["value"] == 1.0
    assert features["future_rows_excluded"] == 1
    missing = build_snapshot_features([], 10)
    assert missing["price"]["state"] == "missing"
    assert missing["price"]["value"] is None


def test_fixture_snapshot_pilot_builds_12_winners_60_controls(tmp_path) -> None:
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    result = build_fixture_snapshots(config)
    assert result["winners"] == 12
    assert result["controls"] == 60
    with ResearchStore(config).connect() as conn:
        assert conn.execute("SELECT COUNT(DISTINCT token_id) AS c FROM research_snapshots").fetchone()["c"] == 72


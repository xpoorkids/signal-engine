from __future__ import annotations

import pytest

from research.config import load_config
from research.matching.controls import MatchCandidate, build_fixture_controls, matching_distance, validate_matching_variables
from research.storage import ResearchStore


def test_matching_rejects_future_variables() -> None:
    with pytest.raises(ValueError, match="future_matching_variable"):
        validate_matching_variables({"future_peak": 10})


def test_winner_cannot_match_itself() -> None:
    item = MatchCandidate("a", "pump", "d", "l", "v", "h", {})
    with pytest.raises(ValueError, match="winner_cannot_match_itself"):
        matching_distance(item, item)


def test_fixture_controls_create_60_matches(tmp_path) -> None:
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    result = build_fixture_controls(config)
    assert result["matches"] == 60
    with ResearchStore(config).connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM research_matches").fetchone()["c"] == 60


from __future__ import annotations

from research.backfill.jobs import bounded_retry_delays, create_or_resume_job, run_fixture_backfill
from research.config import load_config
from research.registry import validate_operator_seeds
from research.storage import ResearchStore


def test_backfill_job_is_deterministic_and_resumable(tmp_path) -> None:
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    first = create_or_resume_job(config, cohort="c", token_id="t", source="s", stage="stage")
    second = create_or_resume_job(config, cohort="c", token_id="t", source="s", stage="stage")
    assert first == second


def test_fixture_backfill_records_source_unavailable_jobs(tmp_path) -> None:
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    validate_operator_seeds(config)
    result = run_fixture_backfill(config, limit=2)
    assert result.source_unavailable == 2
    with ResearchStore(config).connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM research_raw_fetches").fetchone()["c"] == 2


def test_retry_delays_are_bounded_and_deterministic() -> None:
    assert bounded_retry_delays(3, seed=7) == bounded_retry_delays(3, seed=7)
    assert all(0.5 <= value <= 60.5 for value in bounded_retry_delays(8))


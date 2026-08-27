from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResearchConfig:
    db_path: Path
    data_dir: Path
    artifact_dir: Path
    random_seed: int = 1337
    mode: str = "fixture"
    http_timeout_seconds: float = 30.0
    max_concurrency: int = 3
    max_retries: int = 4
    request_budget: int = 1000
    max_pages_per_job: int = 1000
    raw_cache_enabled: bool = True


def load_config(
    *,
    db_path: str | None = None,
    data_dir: str | None = None,
    artifact_dir: str | None = None,
    random_seed: int | None = None,
) -> ResearchConfig:
    mode = os.getenv("SIGNAL_ENGINE_RESEARCH_MODE", "fixture").strip().lower()
    if mode not in {"source", "fixture", "hybrid"}:
        mode = "fixture"
    return ResearchConfig(
        db_path=Path(db_path or os.getenv("SIGNAL_ENGINE_RESEARCH_DB_PATH", "state/research.db")),
        data_dir=Path(data_dir or os.getenv("SIGNAL_ENGINE_RESEARCH_DATA_DIR", "research_data")),
        artifact_dir=Path(artifact_dir or os.getenv("SIGNAL_ENGINE_RESEARCH_ARTIFACT_DIR", "artifacts/research")),
        random_seed=int(random_seed if random_seed is not None else os.getenv("SIGNAL_ENGINE_RESEARCH_RANDOM_SEED", "1337")),
        mode=mode,
        http_timeout_seconds=float(os.getenv("SIGNAL_ENGINE_RESEARCH_HTTP_TIMEOUT_SECONDS", "30")),
        max_concurrency=int(os.getenv("SIGNAL_ENGINE_RESEARCH_MAX_CONCURRENCY", "3")),
        max_retries=int(os.getenv("SIGNAL_ENGINE_RESEARCH_MAX_RETRIES", "4")),
        request_budget=int(os.getenv("SIGNAL_ENGINE_RESEARCH_REQUEST_BUDGET", "1000")),
        max_pages_per_job=int(os.getenv("SIGNAL_ENGINE_RESEARCH_MAX_PAGES_PER_JOB", "1000")),
        raw_cache_enabled=os.getenv("SIGNAL_ENGINE_RESEARCH_RAW_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"},
    )

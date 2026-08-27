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


def load_config(
    *,
    db_path: str | None = None,
    data_dir: str | None = None,
    artifact_dir: str | None = None,
    random_seed: int | None = None,
) -> ResearchConfig:
    return ResearchConfig(
        db_path=Path(db_path or os.getenv("SIGNAL_ENGINE_RESEARCH_DB_PATH", "state/research.db")),
        data_dir=Path(data_dir or os.getenv("SIGNAL_ENGINE_RESEARCH_DATA_DIR", "research_data")),
        artifact_dir=Path(artifact_dir or os.getenv("SIGNAL_ENGINE_RESEARCH_ARTIFACT_DIR", "artifacts/research")),
        random_seed=int(random_seed if random_seed is not None else os.getenv("SIGNAL_ENGINE_RESEARCH_RANDOM_SEED", "1337")),
    )


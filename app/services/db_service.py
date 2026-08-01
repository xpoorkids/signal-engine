from __future__ import annotations

import os
import sqlite3
from pathlib import Path


DEFAULT_BUSY_TIMEOUT_MS = 5000
DEFAULT_ENGINE_DB_PATH = Path("state/engine.db")


def resolve_engine_db_path(default_path: Path | str | None = None) -> Path:
    raw = (
        os.getenv("SIGNAL_ENGINE_DB_PATH", "").strip()
        or os.getenv("STATE_ENGINE_DB_PATH", "").strip()
    )
    if raw:
        return Path(raw)
    if default_path is not None:
        return Path(default_path)
    return DEFAULT_ENGINE_DB_PATH


def connect_sqlite(db_path: Path | str, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = max(0, int(busy_timeout_ms))
    conn = sqlite3.connect(path, timeout=timeout_ms / 1000.0)
    conn.row_factory = sqlite3.Row
    for pragma in (
        "PRAGMA journal_mode=WAL",
        f"PRAGMA busy_timeout={timeout_ms}",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA foreign_keys=ON",
    ):
        try:
            conn.execute(pragma)
        except sqlite3.Error:
            pass
    return conn

from __future__ import annotations

from app.services.db_service import connect_sqlite


def test_connect_sqlite_applies_wal_and_busy_timeout(tmp_path):
    db_path = tmp_path / "engine.db"

    with connect_sqlite(db_path, busy_timeout_ms=4321) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 4321
    assert int(foreign_keys) == 1


def test_connect_sqlite_creates_nested_parent_directories(tmp_path):
    db_path = tmp_path / "nested" / "state" / "engine.db"

    with connect_sqlite(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS smoke (id INTEGER PRIMARY KEY)")

    assert db_path.exists()

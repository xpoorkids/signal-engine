from __future__ import annotations

import sqlite3
import time

from fastapi.testclient import TestClient

from app import main
from app.routes import health as health_route


def test_storage_health_reports_read_and_write_probe_ok(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE signals (signal_id TEXT)")

    client = TestClient(main.app)
    response = client.get("/health/storage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["read_only_connect_ok"] is True
    assert payload["write_probe_ok"] is True
    assert payload["write_probe_error"] is None

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT checked_ts FROM storage_health_probe WHERE id=1").fetchone()
    assert row is not None


def test_storage_health_reports_disk_full_write_probe(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE signals (signal_id TEXT)")

    real_connect = health_route.sqlite3.connect

    def connect_probe(path, *args, **kwargs):
        if kwargs.get("uri"):
            return real_connect(path, *args, **kwargs)
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(health_route.sqlite3, "connect", connect_probe)

    client = TestClient(main.app)
    response = client.get("/health/storage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "storage_error"
    assert payload["read_only_connect_ok"] is True
    assert payload["write_probe_ok"] is False
    assert payload["schema_error"] is None
    assert payload["write_probe_error"] == "OperationalError: database or disk is full"


def test_storage_recover_prunes_old_learning_rows_and_runs_write_probe(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(db_path))
    monkeypatch.setenv("SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN", "secret-token")
    old_ts = int(time.time()) - 40 * 86400
    fresh_ts = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE signal_snapshots (captured_ts INTEGER, snapshot_json TEXT)")
        conn.execute("CREATE TABLE signal_snapshot_jobs (due_ts INTEGER)")
        conn.execute("CREATE TABLE signal_decisions (created_ts INTEGER)")
        conn.execute("CREATE TABLE signals (updated_ts INTEGER)")
        conn.execute("INSERT INTO signal_snapshots VALUES (?, '{}')", (old_ts,))
        conn.execute("INSERT INTO signal_snapshots VALUES (?, '{}')", (fresh_ts,))
        conn.execute("INSERT INTO signal_snapshot_jobs VALUES (?)", (old_ts,))
        conn.execute("INSERT INTO signal_decisions VALUES (?)", (old_ts,))
        conn.execute("INSERT INTO signals VALUES (?)", (old_ts,))

    client = TestClient(main.app)
    denied = client.post(
        "/health/storage/recover",
        headers={"X-Signal-Engine-Token": "wrong"},
        json={"max_age_days": 21, "batch_limit": 100},
    )
    response = client.post(
        "/health/storage/recover",
        headers={"X-Signal-Engine-Token": "secret-token"},
        json={"max_age_days": 21, "batch_limit": 100},
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "recovered"
    assert payload["write_probe"]["ok"] is True
    assert payload["deleted"]["signal_snapshots"] == 1
    with sqlite3.connect(db_path) as conn:
        remaining_snapshots = conn.execute("SELECT COUNT(1) FROM signal_snapshots").fetchone()[0]
        probe = conn.execute("SELECT checked_ts FROM storage_health_probe WHERE id=1").fetchone()
    assert remaining_snapshots == 1
    assert probe is not None


def test_storage_recover_resets_confirmed_malformed_database(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(db_path))
    monkeypatch.setenv("SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN", "secret-token")
    db_path.write_bytes(b"not a sqlite database")
    wal_path = db_path.with_name(db_path.name + "-wal")
    wal_path.write_bytes(b"stale wal")

    client = TestClient(main.app)
    denied = client.post(
        "/health/storage/recover",
        headers={"X-Signal-Engine-Token": "wrong"},
        json={"dry_run": True},
    )
    dry_run = client.post(
        "/health/storage/recover",
        headers={"X-Signal-Engine-Token": "wrong"},
        json={"dry_run": True, "confirm_malformed_storage_reset": True},
    )
    response = client.post(
        "/health/storage/recover",
        headers={"X-Signal-Engine-Token": "wrong"},
        json={"confirm_malformed_storage_reset": True},
    )

    assert denied.status_code == 403
    assert dry_run.status_code == 200
    assert dry_run.json()["status"] == "malformed_reset_available"
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "reset"
    assert payload["write_probe"]["ok"] is True
    assert str(db_path) in payload["removed_files"]
    assert not wal_path.exists()
    with sqlite3.connect(db_path) as conn:
        probe = conn.execute("SELECT checked_ts FROM storage_health_probe WHERE id=1").fetchone()
    assert probe is not None

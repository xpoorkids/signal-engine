from __future__ import annotations

import sqlite3

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

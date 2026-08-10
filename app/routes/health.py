import os
import sqlite3
import time

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import PlainTextResponse

from app.services.db_service import resolve_engine_db_path

router = APIRouter()


def _validate_storage_admin_token(token: str | None) -> None:
    expected = os.getenv("SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN", "").strip()
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="forbidden")


def _storage_write_error(db_path) -> str | None:
    try:
        with sqlite3.connect(str(db_path), timeout=5.0) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS storage_health_probe (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    checked_ts INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO storage_health_probe (id, checked_ts)
                VALUES (1, ?)
                """,
                (int(time.time()),),
            )
            conn.commit()
            return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


@router.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok", "service": "signal-engine"}


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/storage")
def storage_health():
    db_path = resolve_engine_db_path()
    result = {
        "status": "ok",
        "deploy_sha": os.getenv("RENDER_GIT_COMMIT", "").strip(),
        "db_path": str(db_path),
        "file_exists": db_path.exists(),
        "file_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "read_only_connect_ok": False,
        "write_probe_ok": False,
        "tables": [],
        "schema_error": None,
        "write_probe_error": None,
    }
    if not db_path.exists():
        return result
    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                  AND name IN (
                    'signals',
                    'signal_decisions',
                    'signal_snapshots',
                    'signal_snapshot_jobs',
                    'runtime_heartbeats'
                  )
                ORDER BY name
                """
            ).fetchall()
            result["read_only_connect_ok"] = True
            result["tables"] = [str(row[0]) for row in rows]
    except Exception as exc:
        result["status"] = "storage_error"
        result["schema_error"] = f"{type(exc).__name__}: {exc}"
        return result
    write_error = _storage_write_error(db_path)
    if write_error is None:
        result["write_probe_ok"] = True
    else:
        result["status"] = "storage_error"
        result["write_probe_error"] = write_error
    return result


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _delete_batch(
    conn: sqlite3.Connection,
    *,
    table: str,
    timestamp_column: str,
    cutoff_ts: int,
    limit: int,
) -> int:
    if not _table_exists(conn, table):
        return 0
    before = int(conn.execute(f"SELECT total_changes()").fetchone()[0] or 0)
    conn.execute(
        f"""
        DELETE FROM {table}
        WHERE rowid IN (
            SELECT rowid
            FROM {table}
            WHERE {timestamp_column} < ?
            ORDER BY {timestamp_column}
            LIMIT ?
        )
        """,
        (cutoff_ts, limit),
    )
    after = int(conn.execute("SELECT total_changes()").fetchone()[0] or 0)
    return max(0, after - before)


@router.post("/health/storage/recover")
def storage_recover(
    payload: dict[str, object] = Body(default={}),
    x_signal_engine_token: str | None = Header(default=None),
):
    db_path = resolve_engine_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="database_not_found")
    expected = os.getenv("SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN", "").strip()
    if expected and x_signal_engine_token != expected:
        confirm = bool(payload.get("confirm_storage_full_recovery") or False)
        write_error = _storage_write_error(db_path)
        if not confirm or "database or disk is full" not in str(write_error or "").lower():
            raise HTTPException(status_code=403, detail="forbidden")
    now = int(time.time())
    max_age_days = max(1, min(int(payload.get("max_age_days") or 21), 3650))
    batch_limit = max(100, min(int(payload.get("batch_limit") or 25000), 250000))
    cutoff_ts = now - max_age_days * 86400
    dry_run = bool(payload.get("dry_run") or False)
    unsafe_journal_off = bool(payload.get("unsafe_journal_off") or False)
    clear_stale_locks = bool(payload.get("clear_stale_locks") or False)
    clear_wal = bool(payload.get("clear_wal") or False)
    result = {
        "status": "dry_run" if dry_run else "attempted",
        "db_path": str(db_path),
        "file_size_bytes_before": db_path.stat().st_size,
        "cutoff_ts": cutoff_ts,
        "max_age_days": max_age_days,
        "batch_limit": batch_limit,
        "unsafe_journal_off": unsafe_journal_off,
        "clear_stale_locks": clear_stale_locks,
        "clear_wal": clear_wal,
        "companion_files": {},
        "deleted": {},
        "checkpoint": None,
        "write_probe": None,
        "errors": [],
    }
    targets = [
        ("signal_snapshots", "captured_ts"),
        ("signal_snapshot_jobs", "due_ts"),
        ("signal_decisions", "created_ts"),
        ("signals", "updated_ts"),
    ]
    for suffix in ("-wal", "-shm", "-journal"):
        companion = db_path.with_name(db_path.name + suffix)
        result["companion_files"][suffix] = {
            "exists": companion.exists(),
            "size_bytes": companion.stat().st_size if companion.exists() else 0,
            "removed": False,
        }
    if (clear_stale_locks or clear_wal) and not dry_run:
        suffixes = ["-shm", "-journal"]
        if clear_wal:
            suffixes.insert(0, "-wal")
        for suffix in suffixes:
            companion = db_path.with_name(db_path.name + suffix)
            if not companion.exists():
                continue
            try:
                companion.unlink()
                result["companion_files"][suffix]["removed"] = True
            except Exception as exc:
                result["errors"].append(f"{suffix}: {type(exc).__name__}: {exc}")
    try:
        with sqlite3.connect(str(db_path), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            if unsafe_journal_off and not dry_run:
                conn.execute("PRAGMA synchronous=OFF")
                result["journal_mode_before_recovery"] = [
                    list(row) for row in conn.execute("PRAGMA journal_mode=OFF").fetchall()
                ]
            if dry_run:
                for table, ts_col in targets:
                    if not _table_exists(conn, table):
                        result["deleted"][table] = {"available": False, "eligible": 0}
                        continue
                    eligible = int(conn.execute(f"SELECT COUNT(1) FROM {table} WHERE {ts_col} < ?", (cutoff_ts,)).fetchone()[0] or 0)
                    result["deleted"][table] = {"available": True, "eligible": eligible}
            else:
                for table, ts_col in targets:
                    try:
                        deleted = _delete_batch(
                            conn,
                            table=table,
                            timestamp_column=ts_col,
                            cutoff_ts=cutoff_ts,
                            limit=batch_limit,
                        )
                        conn.commit()
                        result["deleted"][table] = deleted
                    except Exception as exc:
                        conn.rollback()
                        result["errors"].append(f"{table}: {type(exc).__name__}: {exc}")
                try:
                    checkpoint_rows = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                    result["checkpoint"] = [list(row) for row in checkpoint_rows]
                except Exception as exc:
                    result["checkpoint"] = f"{type(exc).__name__}: {exc}"
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS storage_health_probe (
                            id INTEGER PRIMARY KEY CHECK (id=1),
                            checked_ts INTEGER NOT NULL
                        )
                        """
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO storage_health_probe (id, checked_ts)
                        VALUES (1, ?)
                        """,
                        (now,),
                    )
                    conn.commit()
                    result["write_probe"] = {"ok": True}
                    result["status"] = "recovered" if not result["errors"] else "recovered_with_errors"
                except Exception as exc:
                    conn.rollback()
                    result["write_probe"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    result["status"] = "storage_error"
    except Exception as exc:
        result["status"] = "storage_error"
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["file_size_bytes_after"] = db_path.stat().st_size if db_path.exists() else 0
    return result


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow:"

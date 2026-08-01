import os
import sqlite3

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.services.db_service import resolve_engine_db_path

router = APIRouter()


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
        "tables": [],
        "schema_error": None,
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


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow:"

import asyncio
import logging
import os
import sqlite3
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.routes import health, scan, score, packet, watch, review, learning

from app.services.db_service import resolve_engine_db_path
from app.services.signal_learning_service import daily_report_worker, observe_recheck_worker, policy_automation_worker, snapshot_worker

os.environ.setdefault("SIGNAL_ENGINE_PROCESS_ROLE", "engine")

app = FastAPI(title="signal-engine")
logger = logging.getLogger(__name__)
_BACKGROUND_TASKS: dict[str, asyncio.Task] = {}

app.include_router(health.router)
app.include_router(scan.router)
app.include_router(score.router)
app.include_router(packet.router)
app.include_router(watch.router)
app.include_router(review.router)
app.include_router(learning.router)


@app.exception_handler(sqlite3.Error)
async def sqlite_error_handler(request: Request, exc: sqlite3.Error) -> JSONResponse:
    if not request.url.path.startswith("/learning/"):
        raise exc
    db_path = resolve_engine_db_path()
    return JSONResponse(
        status_code=503,
        content={
            "status": "storage_unavailable",
            "detail": "Learning storage is unavailable; check /health/storage before trusting learning diagnostics.",
            "db_path": str(db_path),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "storage_health_path": "/health/storage",
        },
    )


@app.on_event("startup")
def log_storage_configuration() -> None:
    db_path = resolve_engine_db_path()
    shared_env_set = bool(os.getenv("SIGNAL_ENGINE_DB_PATH", "").strip() or os.getenv("STATE_ENGINE_DB_PATH", "").strip())
    logger.warning(
        "[startup] engine db_path=%s shared_env=%s",
        db_path,
        "set" if shared_env_set else "unset",
    )
    if not shared_env_set:
        logger.warning(
            "[startup] SIGNAL_ENGINE_DB_PATH is unset; engine may use a local SQLite file that is not shared with worker."
        )


def _background_workers_enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return os.getenv("SIGNAL_ENGINE_ENABLE_BACKGROUND_WORKERS", "1").strip().lower() not in {"0", "false", "no", "off"}


def _storage_write_available() -> bool:
    db_path = resolve_engine_db_path()
    if not db_path.exists():
        return True
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
            return True
    except Exception as exc:
        logger.warning("[startup] storage write probe failed; background workers suppressed error=%s", exc)
        return False


def _policy_automation_worker_enabled() -> bool:
    if not _background_workers_enabled():
        return False
    return os.getenv("SIGNAL_ENGINE_ENABLE_POLICY_AUTOMATION_WORKER", "0").strip().lower() not in {"0", "false", "no", "off"}


def _learning_workers_enabled() -> bool:
    if not _background_workers_enabled():
        return False
    return os.getenv("SIGNAL_ENGINE_ENABLE_LEARNING_WORKERS", "1").strip().lower() not in {"0", "false", "no", "off"}


def _observe_recheck_worker_enabled() -> bool:
    if not _learning_workers_enabled():
        return False
    return os.getenv("SIGNAL_ENGINE_ENABLE_OBSERVE_RECHECK_WORKER", "1").strip().lower() not in {"0", "false", "no", "off"}


def _snapshot_worker_enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    # Snapshot outcomes belong to the authoritative engine database. Keep this
    # enabled independently of legacy background/learning worker switches.
    return os.getenv("SIGNAL_ENGINE_ENABLE_SNAPSHOT_WORKER", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


@app.on_event("startup")
async def start_background_workers() -> None:
    if not _storage_write_available():
        logger.warning("[startup] storage unavailable; learning background workers disabled until next restart")
        return
    if _snapshot_worker_enabled():
        name = "learning_snapshots"
        if name not in _BACKGROUND_TASKS or _BACKGROUND_TASKS[name].done():
            _BACKGROUND_TASKS[name] = asyncio.create_task(snapshot_worker(), name=name)
        logger.warning("[startup] learning snapshot worker enabled")
    else:
        logger.warning("[startup] learning snapshot worker disabled")
    if _learning_workers_enabled():
        name = "learning_daily_report"
        if name not in _BACKGROUND_TASKS or _BACKGROUND_TASKS[name].done():
            _BACKGROUND_TASKS[name] = asyncio.create_task(daily_report_worker(), name=name)
        logger.warning("[startup] learning daily report worker enabled")
    else:
        logger.warning("[startup] learning daily report worker disabled")
    if _observe_recheck_worker_enabled():
        name = "observe_recheck"
        if name not in _BACKGROUND_TASKS or _BACKGROUND_TASKS[name].done():
            _BACKGROUND_TASKS[name] = asyncio.create_task(observe_recheck_worker(), name=name)
        logger.warning("[startup] observe recheck worker enabled")
    else:
        logger.warning("[startup] observe recheck worker disabled")
    if _policy_automation_worker_enabled() and ("policy_automation" not in _BACKGROUND_TASKS or _BACKGROUND_TASKS["policy_automation"].done()):
        _BACKGROUND_TASKS["policy_automation"] = asyncio.create_task(policy_automation_worker(), name="policy_automation_worker")
        logger.warning("[startup] policy automation worker enabled")
    else:
        logger.warning("[startup] policy automation worker disabled")


@app.on_event("shutdown")
async def stop_background_workers() -> None:
    for name, task in list(_BACKGROUND_TASKS.items()):
        if task.done():
            _BACKGROUND_TASKS.pop(name, None)
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            _BACKGROUND_TASKS.pop(name, None)

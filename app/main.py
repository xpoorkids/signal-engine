import asyncio
import logging
import os

from fastapi import FastAPI

from app.routes import health, scan, score, packet, watch, review, learning

from app.services.db_service import resolve_engine_db_path
from app.services.signal_learning_service import policy_automation_worker

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


def _policy_automation_worker_enabled() -> bool:
    if not _background_workers_enabled():
        return False
    return os.getenv("SIGNAL_ENGINE_ENABLE_POLICY_AUTOMATION_WORKER", "0").strip().lower() not in {"0", "false", "no", "off"}


@app.on_event("startup")
async def start_background_workers() -> None:
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

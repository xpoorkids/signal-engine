import logging
import os

from fastapi import FastAPI

from app.routes import health, scan, score, packet, watch, review, learning

from app.services.db_service import resolve_engine_db_path

app = FastAPI(title="signal-engine")
logger = logging.getLogger(__name__)

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

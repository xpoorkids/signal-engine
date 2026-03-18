from __future__ import annotations

import logging

from worker.config import EXECUTION_MODE, SHADOW_EXECUTION_ENABLED, LIVE_EXECUTION_REQUESTED
from app.services import shadow_execution_service


logger = logging.getLogger(__name__)


def maybe_open_shadow_position(event) -> str | None:
    if LIVE_EXECUTION_REQUESTED:
        logger.warning("[shadow-executor-skip] token=%s reason=live_mode_unsupported mode=%s", getattr(event, "token", None), EXECUTION_MODE)
        return None
    if not SHADOW_EXECUTION_ENABLED:
        return None
    if getattr(event, "type", "") != "promoted":
        return None
    try:
        return shadow_execution_service.open_shadow_position(event)
    except Exception:
        logger.exception("[shadow-executor-open-error] token=%s", getattr(event, "token", None))
        return None


async def shadow_monitor_worker() -> None:
    if LIVE_EXECUTION_REQUESTED:
        logger.warning("[shadow-executor-monitor-skip] reason=live_mode_unsupported mode=%s", EXECUTION_MODE)
        return
    await shadow_execution_service.shadow_monitor_worker()

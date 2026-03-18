from __future__ import annotations

import logging

from worker.config import ENABLE_SHADOW_EXECUTION
from app.services import shadow_execution_service


logger = logging.getLogger(__name__)


def maybe_open_shadow_position(event) -> str | None:
    if not ENABLE_SHADOW_EXECUTION:
        return None
    if getattr(event, "type", "") != "promoted":
        return None
    try:
        return shadow_execution_service.open_shadow_position(event)
    except Exception:
        logger.exception("[shadow-executor-open-error] token=%s", getattr(event, "token", None))
        return None


async def shadow_monitor_worker() -> None:
    await shadow_execution_service.shadow_monitor_worker()

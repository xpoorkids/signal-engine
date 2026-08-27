"""
Worker execution boundary for the live Signal Engine.

Purpose
-------
- Owns runtime orchestration after ingestion and promotion logic.
- Consumes raw events from the shared asyncio queue, passes them through
  `worker.promote.process_event()`, applies delivery-specific cooldown and
  quality checks, and performs transport plus post-delivery persistence.
- This file is the bridge between:
  - ingestion (`worker.helius_listener`, DEX scanner thread)
  - routing/scoring (`worker.promote`)
  - delivery (`worker.discord`)
  - learning/state persistence (`app.services.signal_learning_service`,
    `app.services.state_service`)

What this file does at runtime
------------------------------
1. Starts long-running worker tasks:
   - event loop
   - heartbeat loop
   - learning snapshot worker
   - daily report worker
   - ops digest worker
   - rollout verification worker
   - Helius listeners, if enabled
   - DEX scanner thread, if enabled
2. Receives events from the queue.
3. Deduplicates source events by `signature:type:token`.
4. Calls `process_event()` to derive `candidate`, `heating_up`, or `promoted`.
5. Applies event-type-specific cooldown and send suppression.
6. Sends Discord/webhook messages.
7. Persists signal history only after confirmed transport success.

Data flow
---------
Input:
- `Event` objects pushed into the queue by:
  - `worker.helius_listener.start_helius_listeners()`
  - `worker.scanner.run()` through the scanner path
  - scheduled recheck tasks created downstream

Transformations:
- Dedupe is performed first with `is_sig_new()`.
- Routing/enrichment/scoring is delegated to `worker.promote.process_event()`.
- Delivery filtering happens here, not in `worker.promote`:
  - cooldown via `can_alert()`
  - `candidate_send` suppression
  - additional heating-up quality check via `_should_send_heating_up()`

Output:
- Discord delivery attempts through:
  - `send_discord()` for `heating_up` and `promoted`
  - `send_candidate_discord()` for `candidate`
- Learning/state persistence through:
  - `record_signal_event()`
  - `update_candidate_message_id()`
  - `mark_candidate_alert_sent()`
  - `record_wallet_signal()`

Key decision points
-------------------
1. Queue dedupe:
- Dedupe key is `"{signature}:{type}:{token}"` when a signature exists.
- If the dedupe key was seen within `EARLY_DEDUPE_TTL_SEC`, the event is
  dropped before `process_event()` runs.
- Events without signatures are never deduped here.

2. Derived event handling:
- `process_event()` can return multiple derived events for one source event.
- `heating_up` and `promoted` share the non-candidate delivery path.
- `candidate` uses a separate create/edit transport path.

3. Heating-up delivery gate:
- Even if `worker.promote` emits `heating_up`, this file can still suppress it.
- `_should_send_heating_up()` requires one of:
  - at least one KOL hit
  - at least two tracked wallet hits
  - at least one DexScreener boost
  - DEX lifecycle with liquidity >= 15000
  - DEX lifecycle with at least 10 X mentions and 10 authors
- If this check fails, the event is dropped here and never sent.

4. Candidate delivery gate:
- Candidates can be suppressed here even after `worker.promote` emitted them.
- Suppression conditions:
  - candidate cooldown (`candidate:{token}`)
  - `candidate_send == False`
- Candidate edit behavior:
  - if `candidate_edit` is true and `candidate_message_id` exists, runner sends
    a Discord PATCH instead of a create
  - otherwise it sends a create and expects a Discord message id back

5. Persistence after delivery:
- This file now persists emitted signals only after successful transport.
- `send_discord()` returns `bool`.
- `send_candidate_discord()` returns `DeliveryResult(success, message_id)`.
- If delivery fails:
  - the signal is not recorded in learning storage
  - candidate message state is not advanced
  - a warning log is emitted via `[dispatch-skip-persist]`

Failure modes and live-debug implications
-----------------------------------------
- Silent-looking suppression due to cooldown:
  - `can_alert()` updates in-memory cooldown state only.
  - Restarts reset this cooldown memory.
  - Trace candidate suppression with `[candidate-cooldown-skip]`.

- Event dropped before routing:
  - Dedupe can block the event before `process_event()` is called.
  - Trace with `[event-loop-skip] reason=dedupe ...`.

- Promotion/heating emitted by promote but not delivered:
  - Transport failure now prevents persistence.
  - Trace transport with `[discord] send attempt`, `[discord-http]`,
    `[discord-http-error]`, and then `[dispatch-skip-persist]`.

- Candidate emitted but not persisted:
  - This happens on failed candidate transport by design.
  - Check `[discord] candidate send attempt`, candidate send status, and
    `[dispatch-skip-persist]`.

- Worker fatal crash:
  - `main()` catches the top-level exception, logs `[fatal] worker crashed:`,
    and then intentionally holds the process open in an infinite loop.
  - This prevents abrupt process exit but does not self-heal the worker.

- Background task failure visibility:
  - `asyncio.gather(..., return_exceptions=True)` means failed tasks do not
    automatically crash the process.
  - This is operationally important: a listener or background worker can stop
    while the process remains alive.

Logging and observability
-------------------------
Primary trace points emitted here:
- `[event-loop] recv type=... token=... sig=...`
- `[event-loop-skip] reason=dedupe ...`
- `[candidate-cooldown-skip]`
- `[heating-up-skip]`
- `[dispatch-skip-persist]`
- `[heartbeat] worker alive`
- `[startup] worker db_path=...`
- `[startup] worker learning_write_mode=...`
- `[fatal] worker crashed: ...`
- `[worker] crashed but holding process open`

Transport logs emitted in downstream dependency `worker.discord`:
- `[discord] send attempt ...`
- `[discord-http] status=...`
- `[discord-http-error] ...`
- `[discord] candidate send attempt ...`
- `[discord] candidate send ok/failed ...`

How to trace an event end-to-end from this file
-----------------------------------------------
1. Find `[event-loop] recv ...`.
2. Check whether dedupe dropped it via `[event-loop-skip]`.
3. Move into `worker.promote` logs for routing and gating.
4. Return here and inspect:
   - cooldown skip
   - heating-up skip
   - candidate send/edit path
5. Inspect `worker.discord` logs for transport result.
6. Confirm persistence behavior with or without `[dispatch-skip-persist]`.

Dependencies and config
-----------------------
Internal dependencies:
- `worker.promote`
- `worker.state`
- `worker.discord`
- `worker.helius_listener`
- `worker.scanner`
- `app.services.state_service`
- `app.services.signal_learning_service`
- `app.services.tuning_service`

Important config inputs:
- `ENABLE_WS`
- `ENABLE_DEX`
- `EARLY_DEDUPE_TTL_SEC`
- `ALERT_COOLDOWN_SEC`
- `HEATING_UP_ALERT_COOLDOWN_SEC`
- `CANDIDATE_ALERT_COOLDOWN_SEC`
- DB path and learning write env vars read during startup

Gotchas
-------
- Cooldown state is in-memory (`EngineState.cooldown`), not shared durable
  state. Restarting the worker resets cooldowns.
- Signature dedupe is also in-memory and restart-sensitive.
- `candidate_send` comes from `worker.promote`; this file treats it as an
  authoritative suppression flag.
- `record_wallet_signal()` runs before non-candidate transport. Wallet signal
  tracking can therefore advance even if Discord delivery fails.
- Because `asyncio.gather(..., return_exceptions=True)` is used, "process alive"
  does not guarantee every background task is healthy.
"""

import asyncio
import os
import sqlite3
import time
import traceback
import logging
import random
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
os.environ.setdefault("SIGNAL_ENGINE_PROCESS_ROLE", "worker")

from worker.config import (
    ENABLE_WS,
    ENABLE_DEX,
    ENABLE_DISCORD,
    EARLY_DEDUPE_TTL_SEC,
    ALERT_COOLDOWN_SEC,
    HEATING_UP_ALERT_COOLDOWN_SEC,
    CANDIDATE_ALERT_COOLDOWN_SEC,
    DRY_RUN,
    DISCORD_WEBHOOK_URL,
    DISCORD_CANDIDATE_WEBHOOK,
    HELIUS_API_KEY,
    HELIUS_WS_URL,
    HELIUS_RPC_URL,
    ENABLE_BIRDEYE,
    ENABLE_PUMPORTAL,
    ENABLE_X_SIGNAL,
    BIRDEYE_API_KEY,
    X_BEARER_TOKEN,
    X_HEAVY_HANDLES,
    X_HEAVY_AUTHOR_IDS,
    JUPITER_API_KEY,
    TRADE_VALIDATION_ENABLED,
    EARLY_WATCH_RATE_LIMIT_PER_HOUR,
    SIGNAL_ENGINE_WORKER_V2_ENABLED,
    SIGNAL_ENGINE_WORKER_V2_MAX_EVENT_ATTEMPTS,
    SIGNAL_ENGINE_WORKER_V2_EVENT_LEASE_SECONDS,
)
from worker.state import EngineState, is_sig_new, can_alert
from worker.events import Event, as_dict
from worker.promote import process_event
from worker.discord import send_discord, send_discord_result, send_candidate_discord, get_discord_delivery_health
from worker.helius_listener import start_helius_listeners
import worker.helius_listener as helius_listener
from worker.shadow_executor import maybe_open_shadow_position, shadow_monitor_worker
from worker.signal_policy import heating_delivery_decision
import worker.scanner as scanner
from app.services.scan_service import process_scan
from app.services.dex_service import get_dex_source_health
from app.services.state_service import get_candidate_rate_limit_state, record_wallet_signal, init as state_init
from app.services.db_service import resolve_engine_db_path
from app.services.signal_learning_service import (
    init as learning_init,
    record_signal_event,
    record_runtime_heartbeat,
    snapshot_worker,
    daily_report_worker,
    observe_recheck_worker,
)
from app.services.tuning_service import ops_digest_worker, rollout_verification_worker
from app.services.structured_logging import log_event
from worker.x_signal import get_x_signal_health
from app.services.worker_runtime_service import (
    WorkerRuntimeRepository,
    build_event_identity,
    build_worker_instance_id,
    sanitize_error_message,
    worker_v2_enabled,
)

_TASKS: dict[str, asyncio.Task] = {}
_QUEUE: asyncio.Queue | None = None
_DEX_SCAN_LAST_EMIT: dict[str, float] = {}
_WORKER_INSTANCE_ID = build_worker_instance_id()
_WORKER_V2_FATAL_HEALTH: dict[str, Any] = {"healthy": True, "failed_task": None, "error": None}
_OPTIONAL_TASK_RESTART_COUNTS: dict[str, int] = {}
DEX_SCAN_EMIT_COOLDOWN_SEC = int(os.getenv("DEX_SCAN_EMIT_COOLDOWN_SEC", "300"))


def _observe_recheck_worker_enabled() -> bool:
    return os.getenv("SIGNAL_ENGINE_ENABLE_OBSERVE_RECHECK_WORKER", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _storage_write_available(db_path) -> bool:
    path = Path(db_path)
    if not path.exists():
        return True
    try:
        with sqlite3.connect(str(path), timeout=5.0) as conn:
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
        logger.warning("[startup] worker storage write probe failed; runtime suppressed error=%s", exc)
        return False


def _task_health() -> dict[str, dict[str, Any]]:
    health: dict[str, dict[str, Any]] = {}
    for name, task in list(_TASKS.items()):
        item: dict[str, Any] = {
            "done": task.done(),
            "cancelled": task.cancelled(),
        }
        if task.done() and not task.cancelled():
            try:
                exc = task.exception()
            except Exception as err:
                exc = err
            if exc is not None:
                item["error_type"] = type(exc).__name__
                item["error"] = str(exc)[:300]
        health[name] = item
    return health


def _worker_health_metadata() -> dict[str, Any]:
    now = time.time()
    ws_last_activity = float(getattr(helius_listener, "LAST_WS_ACTIVITY", 0.0) or 0.0)
    scan_last_ts = float(getattr(scanner, "LAST_SCAN_TS", 0.0) or 0.0)
    dex_source_health = get_dex_source_health()
    scan_started_ts = float(dex_source_health.get("last_started_ts") or 0.0)
    rate_limit_state = get_candidate_rate_limit_state(max(1, EARLY_WATCH_RATE_LIMIT_PER_HOUR))
    metadata: dict[str, Any] = {
        "deploy_sha": os.getenv("RENDER_GIT_COMMIT", "unknown"),
        "dry_run": DRY_RUN,
        "enable_ws": ENABLE_WS,
        "enable_dex": ENABLE_DEX,
        "enable_discord": ENABLE_DISCORD,
        "discord_webhook_configured": bool(DISCORD_WEBHOOK_URL),
        "discord_candidate_webhook_configured": bool(DISCORD_CANDIDATE_WEBHOOK),
        "helius_api_key_configured": bool(HELIUS_API_KEY),
        "helius_ws_configured": bool(HELIUS_WS_URL),
        "helius_rpc_configured": bool(HELIUS_RPC_URL or os.getenv("HELIUS_HTTPS_RPC_URL")),
        "birdeye_enabled": ENABLE_BIRDEYE,
        "birdeye_api_key_configured": bool(BIRDEYE_API_KEY),
        "pumpportal_enabled": ENABLE_PUMPORTAL,
        "x_signal_enabled": ENABLE_X_SIGNAL,
        "x_bearer_configured": bool(X_BEARER_TOKEN),
        "x_heavy_handles_count": len(X_HEAVY_HANDLES),
        "x_heavy_author_ids_count": len(X_HEAVY_AUTHOR_IDS),
        "jupiter_api_key_configured": bool(JUPITER_API_KEY),
        "trade_validation_enabled": TRADE_VALIDATION_ENABLED,
        "queue_size": _QUEUE.qsize() if _QUEUE is not None else None,
        "queue_max_size": _QUEUE.maxsize if _QUEUE is not None else None,
        "candidate_rate_limit_per_hour": EARLY_WATCH_RATE_LIMIT_PER_HOUR,
        "candidate_rate_limit_effective_per_hour": max(1, EARLY_WATCH_RATE_LIMIT_PER_HOUR),
        "candidate_rate_limit_state": rate_limit_state,
        "tasks": _task_health(),
        "producer_health": {
            "ws_last_activity_age_seconds": round(now - ws_last_activity, 1) if ws_last_activity else None,
            "scanner_last_scan_age_seconds": round(now - scan_last_ts, 1) if scan_last_ts else None,
            "scanner_scan_started_age_seconds": round(now - scan_started_ts, 1) if scan_started_ts else None,
            "scanner_scan_in_progress": bool(dex_source_health.get("in_progress")),
            "scanner_current_source": dex_source_health.get("current_source"),
            "scanner_last_candidate_count": getattr(scanner, "LAST_SCAN_COUNT", None),
            "scanner_last_error": getattr(scanner, "LAST_SCAN_ERROR", None),
            "dex_source_health": dex_source_health,
            "x_signal_health": get_x_signal_health(),
            "discord_delivery_health": get_discord_delivery_health(),
        },
    }
    if worker_v2_enabled():
        repo = WorkerRuntimeRepository()
        try:
            repo.init_schema()
            metadata["worker_v2"] = repo.health_summary(
                worker_v2_enabled=True,
                worker_instance_id=_WORKER_INSTANCE_ID,
                critical_task_health=_WORKER_V2_FATAL_HEALTH,
                optional_task_restart_counts=_OPTIONAL_TASK_RESTART_COUNTS,
            )
        except Exception as exc:
            metadata["worker_v2"] = {
                "enabled": True,
                "worker_instance_id": _WORKER_INSTANCE_ID,
                "health_error_type": type(exc).__name__,
                "health_error": sanitize_error_message(exc),
            }
    else:
        metadata["worker_v2"] = {"enabled": False}
    return metadata


def _create_worker_task(name: str, awaitable) -> asyncio.Task:
    task = asyncio.create_task(awaitable, name=name)
    _TASKS[name] = task
    return task


def _should_send_heating_up(de: Event) -> bool:
    extra = de.extra if isinstance(de.extra, dict) else {}
    allow, reasons = heating_delivery_decision(extra)
    route = extra.get("route_decision") if isinstance(extra.get("route_decision"), dict) else {}
    if not allow:
        log_event(
            logger,
            logging.INFO,
            "heating-up-skip",
            token=de.token,
            tier=route.get("tier") or "unknown",
            blockers=reasons,
            confidence_score=getattr(de, "confidence", None),
        )
    else:
        log_event(
            logger,
            logging.INFO,
            "heating-up-route",
            token=de.token,
            tier=str(route.get("tier") or ""),
            confirmations=reasons,
            confidence_score=getattr(de, "confidence", None),
        )
    return allow


def _non_candidate_cooldown_key(de: Event) -> tuple[str | None, int]:
    if not de.token:
        return None, ALERT_COOLDOWN_SEC
    if de.type == "promoted":
        return f"promoted:{de.token}", ALERT_COOLDOWN_SEC
    extra = de.extra if isinstance(de.extra, dict) else {}
    route = extra.get("route_decision") if isinstance(extra.get("route_decision"), dict) else {}
    tier = str(route.get("tier") or "").strip().lower()
    if de.type == "heating_up" and tier == "sniper":
        return f"sniper:{de.token}", HEATING_UP_ALERT_COOLDOWN_SEC
    return f"{de.type}:{de.token}", HEATING_UP_ALERT_COOLDOWN_SEC


def _derived_event_priority(de: Event) -> int:
    extra = de.extra if isinstance(de.extra, dict) else {}
    route = extra.get("route_decision") if isinstance(extra.get("route_decision"), dict) else {}
    tier = str(route.get("tier") or "").strip().lower()
    if de.type == "promoted":
        return 4
    if de.type == "heating_up" and tier == "sniper":
        return 3
    if de.type == "candidate":
        return 2
    if de.type == "heating_up":
        return 1
    return 0


def _persist_non_candidate_delivery(de: Event, delivered: bool) -> str | None:
    if not delivered:
        log_event(logger, logging.WARNING, "dispatch-skip-persist", type=de.type, token=de.token, reason="delivery_failed")
        return None
    signal_id = record_signal_event(de)
    if isinstance(de.extra, dict) and signal_id:
        de.extra["_signal_id"] = signal_id
    log_event(logger, logging.INFO, "dispatch-persist", type=de.type, token=de.token, signal_id=signal_id, delivered=delivered)
    return signal_id


def _persist_candidate_delivery(de: Event, *, delivered: bool, message_id: str | None, edited: bool) -> None:
    if not delivered:
        log_event(logger, logging.WARNING, "dispatch-skip-persist", type="candidate", token=de.token, reason="delivery_failed")
        return
    external_ref = str(message_id or "")
    record_signal_event(
        de,
        external_ref=external_ref,
        edited=edited,
    )
    log_event(
        logger,
        logging.INFO,
        "dispatch-persist",
        type="candidate",
        token=de.token,
        message_id=message_id,
        edited=edited,
        delivered=delivered,
    )
    if message_id and not edited:
        from app.services.state_service import (
            consume_candidate_rate_limit,
            update_candidate_message_id,
            mark_candidate_alert_sent,
        )
        consume_candidate_rate_limit(max(1, EARLY_WATCH_RATE_LIMIT_PER_HOUR))
        update_candidate_message_id(de.token, message_id)
        mark_candidate_alert_sent(de.token)


def _route_tier(de: Event) -> str | None:
    extra = de.extra if isinstance(de.extra, dict) else {}
    route = extra.get("route_decision") if isinstance(extra.get("route_decision"), dict) else {}
    tier = route.get("tier")
    return str(tier) if tier is not None else None


def _event_payload(de: Event) -> dict[str, Any]:
    payload = as_dict(de)
    payload.pop("id", None)
    return payload


def _delivery_result_disposition(result: Any) -> tuple[str, str]:
    if getattr(result, "success", False):
        return "delivery_sent", "sent"
    reason = str(getattr(result, "reason", "") or "delivery_failed")
    if reason == "dry_run":
        return "dry_run_suppressed", reason
    if reason in {"delivery_disabled", "missing_webhook", "not_candidate_event"}:
        return "delivery_disabled", reason
    if getattr(result, "ambiguous", False):
        return "delivery_uncertain", reason
    return "delivery_failed", reason


def _delivery_suppression_reason(channel: str) -> str | None:
    if not ENABLE_DISCORD:
        return "delivery_disabled"
    if DRY_RUN:
        return "dry_run"
    if channel == "candidate" and not DISCORD_CANDIDATE_WEBHOOK:
        return "missing_candidate_webhook"
    if channel == "main" and not DISCORD_WEBHOOK_URL:
        return "missing_webhook_url"
    return None


async def _handle_derived_event_v2(repo: WorkerRuntimeRepository, source_event: Event, event_id: str, de: Event) -> None:
    tier = _route_tier(de)
    base_payload = {"event": _event_payload(de), "source_event_id": event_id}
    if de.type in ("heating_up", "promoted"):
        cooldown_key, cooldown_sec = _non_candidate_cooldown_key(de)
        decision_id = repo.record_decision(
            event_id=event_id,
            derived_event_type=de.type,
            token=de.token,
            route_tier=tier,
            disposition="derived",
            reason="derived_event",
            payload=base_payload,
        )
        if de.type == "heating_up" and not _should_send_heating_up(de):
            repo.update_decision(decision_id, disposition="quality_suppressed", reason="heating_quality_suppressed")
            return
        suppression = _delivery_suppression_reason("main")
        if suppression:
            disposition = "dry_run_suppressed" if suppression == "dry_run" else "delivery_disabled"
            repo.update_decision(decision_id, disposition=disposition, reason=suppression)
            repo.create_outbox(
                decision_id=decision_id,
                event_id=event_id,
                channel="main",
                operation="post",
                destination_key="main",
                token=de.token,
                event_type=de.type,
                payload=base_payload,
                status="suppressed",
            )
            return
        reservation_id = decision_id
        if de.token and cooldown_key:
            cooldown = repo.reserve_cooldown(
                cooldown_key,
                cooldown_sec,
                reservation_id,
                metadata={"event_id": event_id, "decision_id": decision_id, "event_type": de.type},
            )
            if not cooldown.allowed:
                repo.update_decision(decision_id, disposition="cooldown_suppressed", reason=cooldown.reason)
                return
        delivery_id = repo.create_outbox(
            decision_id=decision_id,
            event_id=event_id,
            channel="main",
            operation="post",
            destination_key="main",
            token=de.token,
            event_type=de.type,
            payload=base_payload,
        )
        repo.update_decision(decision_id, disposition="delivery_pending", delivery_id=delivery_id)
        repo.mark_outbox_attempting(delivery_id)
        result = send_discord_result(de)
        repo.update_outbox_result(delivery_id, result=result)
        disposition, reason = _delivery_result_disposition(result)
        delivered_ts = int(time.time()) if result.success else None
        if result.success:
            if de.token and cooldown_key:
                repo.commit_cooldown(cooldown_key, reservation_id, delivered_ts=delivered_ts)
            buyer = de.extra.get("buyer") if isinstance(de.extra, dict) else None
            if isinstance(buyer, str) and buyer:
                record_wallet_signal(buyer, de.token or "", de.type)
            signal_id = _persist_non_candidate_delivery(de, True)
            repo.update_decision(decision_id, disposition=disposition, reason=reason, delivered_ts=delivered_ts, legacy_signal_id=signal_id)
            if signal_id:
                maybe_open_shadow_position(de)
        else:
            if de.token and cooldown_key and not getattr(result, "ambiguous", False):
                repo.release_cooldown(cooldown_key, reservation_id, reason=reason)
            repo.update_decision(decision_id, disposition=disposition, reason=reason)
            log_event(logger, logging.WARNING, "dispatch-skip-persist", type=de.type, token=de.token, reason=reason, event_id=event_id)
        return

    if de.type == "candidate":
        decision_id = repo.record_decision(
            event_id=event_id,
            derived_event_type=de.type,
            token=de.token,
            route_tier=tier,
            disposition="derived",
            reason="derived_event",
            payload=base_payload,
        )
        if isinstance(de.extra, dict) and de.extra.get("candidate_send") is False:
            repo.update_decision(decision_id, disposition="candidate_send_suppressed", reason="candidate_send_false")
            return
        suppression = _delivery_suppression_reason("candidate")
        if suppression:
            disposition = "dry_run_suppressed" if suppression == "dry_run" else "delivery_disabled"
            repo.update_decision(decision_id, disposition=disposition, reason=suppression)
            repo.create_outbox(
                decision_id=decision_id,
                event_id=event_id,
                channel="candidate",
                operation="edit" if isinstance(de.extra, dict) and de.extra.get("candidate_edit") else "post",
                destination_key="candidate",
                token=de.token,
                event_type=de.type,
                payload=base_payload,
                edit_message_id=str(de.extra.get("candidate_message_id")) if isinstance(de.extra, dict) and de.extra.get("candidate_message_id") else None,
                status="suppressed",
            )
            return
        cooldown_key = f"candidate:{de.token}" if de.token else None
        if de.token and cooldown_key:
            cooldown = repo.reserve_cooldown(
                cooldown_key,
                CANDIDATE_ALERT_COOLDOWN_SEC,
                decision_id,
                metadata={"event_id": event_id, "decision_id": decision_id, "event_type": de.type},
            )
            if not cooldown.allowed:
                repo.update_decision(decision_id, disposition="cooldown_suppressed", reason=cooldown.reason)
                return
        message_id = None
        if isinstance(de.extra, dict) and de.extra.get("candidate_edit") and de.extra.get("candidate_message_id"):
            message_id = de.extra.get("candidate_message_id")
        delivery_id = repo.create_outbox(
            decision_id=decision_id,
            event_id=event_id,
            channel="candidate",
            operation="edit" if message_id else "post",
            destination_key="candidate",
            token=de.token,
            event_type=de.type,
            payload=base_payload,
            edit_message_id=message_id,
        )
        repo.update_decision(decision_id, disposition="delivery_pending", delivery_id=delivery_id)
        repo.mark_outbox_attempting(delivery_id)
        result = send_candidate_discord(de, message_id=message_id)
        repo.update_outbox_result(delivery_id, result=result)
        disposition, reason = _delivery_result_disposition(result)
        delivered_ts = int(time.time()) if result.success else None
        if result.success:
            if de.token and cooldown_key:
                repo.commit_cooldown(cooldown_key, decision_id, delivered_ts=delivered_ts)
            _persist_candidate_delivery(de, delivered=True, message_id=result.message_id, edited=bool(message_id))
            repo.update_decision(decision_id, disposition=disposition, reason=reason, delivered_ts=delivered_ts)
            maybe_open_shadow_position(de)
        else:
            if de.token and cooldown_key and not getattr(result, "ambiguous", False):
                repo.release_cooldown(cooldown_key, decision_id, reason=reason)
            repo.update_decision(decision_id, disposition=disposition, reason=reason)
            log_event(logger, logging.WARNING, "dispatch-skip-persist", type="candidate", token=de.token, reason=reason, event_id=event_id)


async def _process_event_v2(repo: WorkerRuntimeRepository, state: EngineState, e: Event) -> None:
    event_id = build_event_identity(e)
    claim = repo.claim_event(
        e,
        worker_id=_WORKER_INSTANCE_ID,
        lease_seconds=SIGNAL_ENGINE_WORKER_V2_EVENT_LEASE_SECONDS,
        max_attempts=SIGNAL_ENGINE_WORKER_V2_MAX_EVENT_ATTEMPTS,
    )
    if not claim.claimed:
        log_event(
            logger,
            logging.INFO,
            "event-loop-skip",
            reason=claim.reason,
            type=e.type,
            token=e.token,
            sig=e.signature,
            event_id=event_id,
            status=claim.status,
        )
        return
    try:
        derived = sorted(await process_event(state, e), key=_derived_event_priority, reverse=True)
        for de in derived:
            await _handle_derived_event_v2(repo, e, event_id, de)
        repo.complete_event(event_id)
        repo.advance_checkpoint(
            f"source:{e.source}:completed",
            source=e.source,
            stage="completed",
            slot=e.slot,
            signature=e.signature,
            event_id=event_id,
            observed_ts=e.ts,
            metadata={"event_type": e.type},
        )
        repo.advance_checkpoint(
            "worker:event_loop:completed",
            source=e.source,
            stage="event_loop_completed",
            slot=e.slot,
            signature=e.signature,
            event_id=event_id,
            observed_ts=e.ts,
            metadata={"event_type": e.type},
        )
    except Exception as ex:
        repo.fail_event(
            event_id,
            error=ex,
            failure_stage="process_event",
            max_attempts=SIGNAL_ENGINE_WORKER_V2_MAX_EVENT_ATTEMPTS,
        )
        log_event(
            logger,
            logging.ERROR,
            "event-loop-error",
            type=e.type,
            token=e.token,
            sig=e.signature,
            event_id=event_id,
            error_type=type(ex).__name__,
            error=sanitize_error_message(ex),
        )
        raise


async def event_loop(q: asyncio.Queue) -> None:
    """
    Consume queued events, route them through `worker.promote`, and execute the
    delivery/persistence contract for derived events.
    """
    state = EngineState()
    state_init()
    learning_init()
    repo = WorkerRuntimeRepository() if worker_v2_enabled() else None
    if repo is not None:
        repo.init_schema()
    while True:
        e: Event = await q.get()
        dedupe_sig = f"{e.signature}:{e.type}:{e.token or ''}" if e.signature else None
        try:
            log_event(logger, logging.INFO, "event-loop", action="recv", type=e.type, token=e.token, sig=e.signature)
            if repo is not None:
                await _process_event_v2(repo, state, e)
                continue
            if not is_sig_new(state, dedupe_sig, EARLY_DEDUPE_TTL_SEC):
                log_event(logger, logging.INFO, "event-loop-skip", reason="dedupe", type=e.type, token=e.token, sig=e.signature)
                continue

            derived = sorted(await process_event(state, e), key=_derived_event_priority, reverse=True)
            for de in derived:
                if de.type in ("heating_up", "promoted"):
                    cooldown_key, cooldown_sec = _non_candidate_cooldown_key(de)
                    if de.token and cooldown_key and not can_alert(state, cooldown_key, cooldown_sec):
                        route = de.extra.get("route_decision") if isinstance(de.extra, dict) and isinstance(de.extra.get("route_decision"), dict) else {}
                        log_event(
                            logger,
                            logging.INFO,
                            "dispatch-cooldown-skip",
                            type=de.type,
                            token=de.token,
                            tier=route.get("tier") or "",
                            key=cooldown_key,
                            cooldown_sec=cooldown_sec,
                        )
                        continue
                    if de.type == "heating_up" and not _should_send_heating_up(de):
                        continue
                    buyer = de.extra.get("buyer") if isinstance(de.extra, dict) else None
                    delivered = send_discord(de)
                    if delivered and isinstance(buyer, str) and buyer:
                        record_wallet_signal(buyer, de.token or "", de.type)
                    signal_id = _persist_non_candidate_delivery(de, delivered)
                    if delivered and signal_id:
                        maybe_open_shadow_position(de)
                elif de.type == "candidate":
                    if de.token and not can_alert(state, f"candidate:{de.token}", CANDIDATE_ALERT_COOLDOWN_SEC):
                        log_event(
                            logger,
                            logging.INFO,
                            "candidate-cooldown-skip",
                            token=de.token,
                            cooldown_key=f"candidate:{de.token}",
                            cooldown_sec=CANDIDATE_ALERT_COOLDOWN_SEC,
                        )
                        continue
                    if isinstance(de.extra, dict) and de.extra.get("candidate_send") is False:
                        route = de.extra.get("route_decision") if isinstance(de.extra.get("route_decision"), dict) else {}
                        log_event(
                            logger,
                            logging.INFO,
                            "candidate-send-skip",
                            token=de.token,
                            reason="candidate_send_false",
                            route_tier=route.get("tier"),
                            route_blockers=route.get("blockers") or [],
                            candidate_send_reasons=(de.extra.get("candidate_send_reasons") or []),
                        )
                        continue
                    message_id = None
                    if isinstance(de.extra, dict):
                        if de.extra.get("candidate_edit") and de.extra.get("candidate_message_id"):
                            message_id = de.extra.get("candidate_message_id")
                    delivery = send_candidate_discord(de, message_id=message_id)
                    _persist_candidate_delivery(
                        de,
                        delivered=delivery.success,
                        message_id=delivery.message_id,
                        edited=bool(message_id),
                    )
                    if delivery.success:
                        maybe_open_shadow_position(de)
        except Exception as ex:
            log_event(
                logger,
                logging.ERROR,
                "event-loop-error",
                type=e.type,
                token=e.token,
                sig=e.signature,
                error_type=type(ex).__name__,
                error=str(ex),
            )
            traceback.print_exc()
        finally:
            q.task_done()


async def heartbeat_loop() -> None:
    last_heartbeat = 0.0
    while True:
        if time.time() - last_heartbeat > 30:
            persisted = record_runtime_heartbeat(
                service_role="worker",
                metadata=_worker_health_metadata(),
            )
            logger.info("[heartbeat] worker alive persisted=%s", persisted)
            last_heartbeat = time.time()
        await asyncio.sleep(1)


async def dex_scan_loop(q: asyncio.Queue) -> None:
    while True:
        try:
            hits = await asyncio.to_thread(process_scan)
            scanner.LAST_SCAN_TS = time.time()
            scanner.LAST_SCAN_COUNT = len(hits)
            scanner.LAST_SCAN_ERROR = None
            log_event(logger, logging.INFO, "dex-scan", candidates=len(hits))
            dex_health = get_dex_source_health()
            for candidate in hits:
                token = str(candidate.get("token") or "").strip()
                if not token:
                    continue
                now = time.time()
                last_emit = _DEX_SCAN_LAST_EMIT.get(token, 0.0)
                if now - last_emit < DEX_SCAN_EMIT_COOLDOWN_SEC:
                    continue
                _DEX_SCAN_LAST_EMIT[token] = now
                metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
                metrics = {**metrics, "dex_source_health": dex_health}
                await q.put(
                    Event(
                        type="token_resolved",
                        source="dex_scan",
                        token=token,
                        confidence=0.55 if candidate.get("reason") == "dex_momentum_watch" else 0.65,
                        reasons=[str(candidate.get("reason") or "dex_scan")],
                        extra={
                            "metrics": metrics,
                            "symbol": candidate.get("symbol"),
                            "dex_scan_candidate": candidate,
                        },
                    )
                )
        except Exception as exc:
            scanner.LAST_SCAN_ERROR = f"{type(exc).__name__}: {exc}"
            log_event(logger, logging.ERROR, "dex-scan-error", error_type=type(exc).__name__, error=str(exc))
        await asyncio.sleep(max(5, int(getattr(scanner, "SCAN_INTERVAL", 30) or 30)))


class WorkerTaskFailure(RuntimeError):
    pass


async def _optional_task_supervisor(name: str, awaitable_factory, *, max_restarts: int = 3, base_delay: float = 0.25) -> None:
    restarts = 0
    while True:
        try:
            result = await awaitable_factory()
            raise WorkerTaskFailure(f"optional task {name} returned unexpectedly: {result!r}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            restarts += 1
            _OPTIONAL_TASK_RESTART_COUNTS[name] = restarts
            log_event(logger, logging.WARNING, "worker-optional-task-failed", task=name, restart_count=restarts, error_type=type(exc).__name__, error=sanitize_error_message(exc))
            if restarts > max_restarts:
                raise WorkerTaskFailure(f"optional task {name} exceeded restart budget") from exc
            delay = min(30.0, base_delay * (2 ** (restarts - 1))) + random.uniform(0, base_delay)
            await asyncio.sleep(delay)


async def _run_worker_v2_supervised(tasks: list[asyncio.Task], critical_names: set[str]) -> None:
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                name = task.get_name()
                if task.cancelled():
                    if name in critical_names:
                        raise WorkerTaskFailure(f"critical task {name} cancelled")
                    continue
                exc = task.exception()
                if exc is not None:
                    if name in critical_names:
                        raise WorkerTaskFailure(f"critical task {name} failed") from exc
                    log_event(logger, logging.ERROR, "worker-optional-task-stopped", task=name, error_type=type(exc).__name__, error=sanitize_error_message(exc))
                    continue
                if name in critical_names:
                    raise WorkerTaskFailure(f"critical task {name} returned unexpectedly")
    except BaseException as exc:
        _WORKER_V2_FATAL_HEALTH.update({"healthy": False, "failed_task": getattr(exc, "__context__", None) and type(exc.__context__).__name__, "error": sanitize_error_message(exc)})
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def run_worker() -> None:
    """
    Start the full worker runtime: queue consumer, background reporting tasks,
    and live event producers.
    """
    log_event(logger, logging.INFO, "worker", action="startup", deploy_sha=os.getenv("RENDER_GIT_COMMIT", "unknown"))
    db_path = resolve_engine_db_path()
    learning_base_url = os.getenv("SIGNAL_ENGINE_LEARNING_WRITE_BASE_URL", "").strip() or os.getenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "").strip()
    learning_mode = os.getenv("SIGNAL_ENGINE_LEARNING_WRITE_MODE", "").strip().lower() or "auto"
    shared_env_set = bool(os.getenv("SIGNAL_ENGINE_DB_PATH", "").strip() or os.getenv("STATE_ENGINE_DB_PATH", "").strip())
    logger.warning("[startup] worker db_path=%s shared_env=%s", db_path, "set" if shared_env_set else "unset")
    logger.warning(
        "[startup] worker learning_write_mode=%s remote_base_configured=%s",
        learning_mode,
        "yes" if learning_base_url else "no",
    )
    if not shared_env_set:
        logger.warning(
            "[startup] SIGNAL_ENGINE_DB_PATH is unset; worker may write to a local SQLite file that is not shared with engine."
        )
    if not _storage_write_available(db_path):
        log_event(
            logger,
            logging.ERROR,
            "worker",
            action="storage_unavailable_fatal" if worker_v2_enabled() else "storage_unavailable_hold",
            db_path=str(db_path),
        )
        if worker_v2_enabled():
            raise RuntimeError("worker_v2_storage_unavailable")
        while True:
            await asyncio.sleep(60)
    global _QUEUE
    tasks = []
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    _QUEUE = q

    learning_init()
    if worker_v2_enabled():
        repo = WorkerRuntimeRepository(db_path)
        repo.init_schema()

    critical_names = {"event_loop", "heartbeat_loop"} if worker_v2_enabled() else set()
    tasks.append(_create_worker_task("event_loop", event_loop(q)))
    tasks.append(_create_worker_task("heartbeat_loop", heartbeat_loop()))

    if worker_v2_enabled():
        tasks.append(_create_worker_task("snapshot_worker", _optional_task_supervisor("snapshot_worker", snapshot_worker)))
        tasks.append(_create_worker_task("daily_report_worker", _optional_task_supervisor("daily_report_worker", daily_report_worker)))
        if _observe_recheck_worker_enabled():
            tasks.append(_create_worker_task("observe_recheck_worker", _optional_task_supervisor("observe_recheck_worker", observe_recheck_worker)))
        tasks.append(_create_worker_task("ops_digest_worker", _optional_task_supervisor("ops_digest_worker", ops_digest_worker)))
        tasks.append(_create_worker_task("rollout_verification_worker", _optional_task_supervisor("rollout_verification_worker", rollout_verification_worker)))
        tasks.append(_create_worker_task("shadow_monitor_worker", _optional_task_supervisor("shadow_monitor_worker", shadow_monitor_worker)))
        if ENABLE_WS:
            critical_names.add("helius_listener")
            tasks.append(_create_worker_task("helius_listener", start_helius_listeners(q)))
        if ENABLE_DEX:
            tasks.append(_create_worker_task("dex_scanner", _optional_task_supervisor("dex_scanner", lambda: dex_scan_loop(q))))
        await _run_worker_v2_supervised(tasks, critical_names)
    else:
        tasks.append(_create_worker_task("snapshot_worker", snapshot_worker()))
        tasks.append(_create_worker_task("daily_report_worker", daily_report_worker()))
        if _observe_recheck_worker_enabled():
            tasks.append(_create_worker_task("observe_recheck_worker", observe_recheck_worker()))
        tasks.append(_create_worker_task("ops_digest_worker", ops_digest_worker()))
        tasks.append(_create_worker_task("rollout_verification_worker", rollout_verification_worker()))
        tasks.append(_create_worker_task("shadow_monitor_worker", shadow_monitor_worker()))
        if ENABLE_WS:
            tasks.append(_create_worker_task("helius_listener", start_helius_listeners(q)))
        if ENABLE_DEX:
            tasks.append(_create_worker_task("dex_scanner", dex_scan_loop(q)))
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(run_worker())
    except Exception as e:
        log_event(logger, logging.ERROR, "fatal", component="worker", error_type=type(e).__name__, error=str(e))
        traceback.print_exc()
        if worker_v2_enabled():
            raise SystemExit(1) from e

    # CRITICAL: never exit
    while True:
        log_event(logger, logging.ERROR, "worker", action="crashed_hold_open")
        time.sleep(60)


if __name__ == "__main__":
    main()

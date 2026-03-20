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
import time
import traceback
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
os.environ.setdefault("SIGNAL_ENGINE_PROCESS_ROLE", "worker")

from worker.config import (
    ENABLE_WS,
    ENABLE_DEX,
    EARLY_DEDUPE_TTL_SEC,
    ALERT_COOLDOWN_SEC,
    HEATING_UP_ALERT_COOLDOWN_SEC,
    CANDIDATE_ALERT_COOLDOWN_SEC,
)
from worker.state import EngineState, is_sig_new, can_alert
from worker.events import Event
from worker.promote import process_event
from worker.discord import send_discord, send_candidate_discord
from worker.helius_listener import start_helius_listeners
from worker.shadow_executor import maybe_open_shadow_position, shadow_monitor_worker
from worker.signal_policy import heating_delivery_decision
import worker.scanner as scanner
from app.services.state_service import record_wallet_signal, init as state_init
from app.services.db_service import resolve_engine_db_path
from app.services.signal_learning_service import (
    init as learning_init,
    record_signal_event,
    snapshot_worker,
    daily_report_worker,
)
from app.services.tuning_service import ops_digest_worker, rollout_verification_worker
from app.services.structured_logging import log_event


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
        from app.services.state_service import update_candidate_message_id, mark_candidate_alert_sent
        update_candidate_message_id(de.token, message_id)
        mark_candidate_alert_sent(de.token)


async def event_loop(q: asyncio.Queue) -> None:
    """
    Consume queued events, route them through `worker.promote`, and execute the
    delivery/persistence contract for derived events.
    """
    state = EngineState()
    state_init()
    learning_init()
    while True:
        e: Event = await q.get()
        dedupe_sig = f"{e.signature}:{e.type}:{e.token or ''}" if e.signature else None
        try:
            log_event(logger, logging.INFO, "event-loop", action="recv", type=e.type, token=e.token, sig=e.signature)
            if not is_sig_new(state, dedupe_sig, EARLY_DEDUPE_TTL_SEC):
                log_event(logger, logging.INFO, "event-loop-skip", reason="dedupe", type=e.type, token=e.token, sig=e.signature)
                q.task_done()
                continue

            derived = await process_event(state, e)
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
                    if isinstance(buyer, str) and buyer:
                        record_wallet_signal(buyer, de.token or "", de.type)
                    delivered = send_discord(de)
                    signal_id = _persist_non_candidate_delivery(de, delivered)
                    if delivered and signal_id and de.type == "promoted":
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
            logger.info("[heartbeat] worker alive")
            last_heartbeat = time.time()
        await asyncio.sleep(1)


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
    tasks = []
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)

    learning_init()
    tasks.append(asyncio.create_task(event_loop(q)))
    tasks.append(asyncio.create_task(heartbeat_loop()))
    tasks.append(asyncio.create_task(snapshot_worker()))
    tasks.append(asyncio.create_task(daily_report_worker()))
    tasks.append(asyncio.create_task(ops_digest_worker()))
    tasks.append(asyncio.create_task(rollout_verification_worker()))
    tasks.append(asyncio.create_task(shadow_monitor_worker()))
    if ENABLE_WS:
        tasks.append(asyncio.create_task(start_helius_listeners(q)))
    if ENABLE_DEX:
        tasks.append(asyncio.to_thread(scanner.run))

    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(run_worker())
    except Exception as e:
        log_event(logger, logging.ERROR, "fatal", component="worker", error_type=type(e).__name__, error=str(e))
        traceback.print_exc()

    # CRITICAL: never exit
    while True:
        log_event(logger, logging.ERROR, "worker", action="crashed_hold_open")
        time.sleep(60)


if __name__ == "__main__":
    main()

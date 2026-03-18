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


def _should_send_heating_up(de: Event) -> bool:
    extra = de.extra if isinstance(de.extra, dict) else {}
    metrics = extra.get("attention_metrics") if isinstance(extra.get("attention_metrics"), dict) else {}
    lifecycle = str(extra.get("lifecycle") or "")
    dex_summary = extra.get("dex_summary") if isinstance(extra.get("dex_summary"), dict) else {}
    tracked_hits = int(metrics.get("tracked_wallet_hits") or 0)
    kol_hits = int(metrics.get("kol_wallet_hits") or 0)
    x_mentions = int(metrics.get("x_tweet_count") or 0)
    x_authors = int(metrics.get("x_unique_authors") or 0)
    boosts = int(metrics.get("dexscreener_boosts_count") or 0)
    liq = float((dex_summary or {}).get("liquidity_usd") or 0.0)
    strong_quality = (
        kol_hits >= 1
        or tracked_hits >= 2
        or boosts >= 1
        or (lifecycle == "dex" and liq >= 15000)
        or (x_mentions >= 10 and x_authors >= 10 and lifecycle == "dex")
    )
    if not strong_quality:
        print(
            f"[heating-up-skip] token={de.token} lifecycle={lifecycle or 'unknown'} "
            f"tracked={tracked_hits} kol={kol_hits} x_mentions={x_mentions} x_authors={x_authors} boosts={boosts} liq={liq:.0f}",
            flush=True,
        )
    return strong_quality


def _persist_non_candidate_delivery(de: Event, delivered: bool) -> None:
    if not delivered:
        logger.warning("[dispatch-skip-persist] type=%s token=%s reason=delivery_failed", de.type, de.token)
        return
    record_signal_event(de)


def _persist_candidate_delivery(de: Event, *, delivered: bool, message_id: str | None, edited: bool) -> None:
    if not delivered:
        logger.warning("[dispatch-skip-persist] type=candidate token=%s reason=delivery_failed", de.token)
        return
    external_ref = str(message_id or "")
    record_signal_event(
        de,
        external_ref=external_ref,
        edited=edited,
    )
    if message_id and not edited:
        from app.services.state_service import update_candidate_message_id, mark_candidate_alert_sent
        update_candidate_message_id(de.token, message_id)
        mark_candidate_alert_sent(de.token)


async def event_loop(q: asyncio.Queue) -> None:
    state = EngineState()
    state_init()
    learning_init()
    while True:
        e: Event = await q.get()
        dedupe_sig = f"{e.signature}:{e.type}:{e.token or ''}" if e.signature else None
        try:
            print(f"[event-loop] recv type={e.type} token={e.token} sig={e.signature}", flush=True)
            if not is_sig_new(state, dedupe_sig, EARLY_DEDUPE_TTL_SEC):
                print(f"[event-loop-skip] reason=dedupe type={e.type} token={e.token} sig={e.signature}", flush=True)
                q.task_done()
                continue

            derived = await process_event(state, e)
            for de in derived:
                if de.type in ("heating_up", "promoted"):
                    cooldown_sec = HEATING_UP_ALERT_COOLDOWN_SEC if de.type == "heating_up" else ALERT_COOLDOWN_SEC
                    if de.token and can_alert(state, f"{de.type}:{de.token}", cooldown_sec):
                        if isinstance(de.extra, dict) and de.extra.get("candidate_send") is False:
                            continue
                        if de.type == "heating_up" and not _should_send_heating_up(de):
                            continue
                        buyer = de.extra.get("buyer") if isinstance(de.extra, dict) else None
                        if isinstance(buyer, str) and buyer:
                            record_wallet_signal(buyer, de.token or "", de.type)
                        delivered = send_discord(de)
                        _persist_non_candidate_delivery(de, delivered)
                elif de.type == "candidate":
                    if de.token and not can_alert(state, f"candidate:{de.token}", CANDIDATE_ALERT_COOLDOWN_SEC):
                        print(f"[candidate-cooldown-skip] token={de.token}", flush=True)
                        continue
                    if isinstance(de.extra, dict) and de.extra.get("candidate_send") is False:
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
            print(f"[event-loop-error] type={e.type} token={e.token} sig={e.signature} error={type(ex).__name__}:{ex}", flush=True)
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
    print(f"[worker] deploy_sha={os.getenv('RENDER_GIT_COMMIT', 'unknown')}", flush=True)
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
    if ENABLE_WS:
        tasks.append(asyncio.create_task(start_helius_listeners(q)))
    if ENABLE_DEX:
        tasks.append(asyncio.to_thread(scanner.run))

    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(run_worker())
    except Exception as e:
        print("[fatal] worker crashed:", e)
        traceback.print_exc()

    # CRITICAL: never exit
    while True:
        print("[worker] crashed but holding process open")
        time.sleep(60)


if __name__ == "__main__":
    main()
logger = logging.getLogger(__name__)

import asyncio
import os
import time
import traceback
import logging

logging.basicConfig(level=logging.INFO)

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
        or (x_mentions >= 5 and x_authors >= 3)
        or boosts >= 1
        or (lifecycle == "dex" and liq >= 8000)
    )
    if not strong_quality:
        print(
            f"[heating-up-skip] token={de.token} lifecycle={lifecycle or 'unknown'} "
            f"tracked={tracked_hits} kol={kol_hits} x_mentions={x_mentions} x_authors={x_authors} boosts={boosts} liq={liq:.0f}",
            flush=True,
        )
    return strong_quality


async def event_loop(q: asyncio.Queue) -> None:
    state = EngineState()
    state_init()
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
                        send_discord(de)
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
                    msg_id = send_candidate_discord(de, message_id=message_id)
                    if msg_id:
                        from app.services.state_service import update_candidate_message_id, mark_candidate_alert_sent
                        update_candidate_message_id(de.token, msg_id)
                        mark_candidate_alert_sent(de.token)
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
    tasks = []
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)

    tasks.append(asyncio.create_task(event_loop(q)))
    tasks.append(asyncio.create_task(heartbeat_loop()))
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

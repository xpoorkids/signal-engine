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
)
from worker.state import EngineState, is_sig_new, can_alert
from worker.events import Event
from worker.promote import process_event
from worker.discord import send_discord, send_candidate_discord
from worker.helius_listener import start_helius_listeners
import worker.scanner as scanner


async def event_loop(q: asyncio.Queue) -> None:
    state = EngineState()
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
                    if de.token and can_alert(state, de.token, ALERT_COOLDOWN_SEC):
                        if isinstance(de.extra, dict) and de.extra.get("candidate_send") is False:
                            continue
                        send_discord(de)
                elif de.type == "candidate":
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

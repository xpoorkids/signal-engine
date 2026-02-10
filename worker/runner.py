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
        if not is_sig_new(state, e.signature, EARLY_DEDUPE_TTL_SEC):
            q.task_done()
            continue

        derived = await process_event(state, e)
        for de in derived:
            if de.type in ("heating_up", "promoted"):
                if de.token and can_alert(state, de.token, ALERT_COOLDOWN_SEC):
                    send_discord(de)
            elif de.type == "candidate":
                send_candidate_discord(de)

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

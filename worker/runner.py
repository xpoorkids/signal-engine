import asyncio
import os
import time
import traceback

from worker.helius_listener import listen
import worker.scanner as scanner


def process_early_candidate(event: dict) -> None:
    """
    Placeholder for early candidate injection.
    """
    _ = event


async def handle_new_pool(event: dict) -> None:
    candidate = {
        "token": event["token"],
        "symbol": "NEW",
        "reason": "helius_new_pool",
        "metrics": {
            "liquidity": 0,
            "volume_5m": 0,
            "price_change_5m": 0,
            "age_minutes": 0,
        },
        "pool": event.get("pool"),
        "signature": event.get("signature"),
    }

    scanner.process_early_candidate(candidate)


async def run_worker() -> None:
    enable_ws = os.getenv("ENABLE_WS", "true").lower() in ("1", "true", "yes")
    tasks = [asyncio.to_thread(scanner.run)]
    if enable_ws:
        tasks.append(listen(handle_new_pool))

    await asyncio.gather(*tasks)


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

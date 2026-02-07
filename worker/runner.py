import asyncio

from worker.helius_listener import listen
from worker.scanner import process_early_candidate, run


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

    process_early_candidate(candidate)


async def main() -> None:
    await asyncio.gather(
        listen(handle_new_pool),
        asyncio.to_thread(run),
    )


if __name__ == "__main__":
    asyncio.run(main())

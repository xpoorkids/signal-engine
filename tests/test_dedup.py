import asyncio
from worker.helius_listener import LogSwapProcessor, _recent_buy_signatures, DEDUP_TTL_SECONDS


async def _run_emit(proc, sig, buy):
    tx = {"slot": 1}
    await proc._emit_trade_buy(sig, tx, buy, "buyer", 0, 0.5, "sol", 0)


def test_dedup_skip():
    _recent_buy_signatures.clear()
    events = []

    async def emit_event(e):
        events.append(e)

    proc = LogSwapProcessor(emit_event)
    buy = {"mint": "TEST", "buyer": "buyer", "delta_raw": 10, "decimals": 0}

    asyncio.run(_run_emit(proc, "sig1", buy))
    asyncio.run(_run_emit(proc, "sig1", buy))

    assert len(events) == 1

import os
import json
import asyncio
import re
import websockets
from datetime import datetime, timezone

HELIUS_KEY = os.getenv("HELIUS_API_KEY")
HELIUS_WS = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# Pump.fun program ID (mainnet)
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Raydium + Orca + Pump.fun program IDs
PROGRAM_IDS = [
    "RVKd61ztZW9L5GxF3XH9RZy5D3R1xYbC5nZ5qZpZr2D",  # Raydium AMM (example)
    "whirLb8k1ZrZg2KqYF9rXy2rZpZqv2X6kz5n",          # Orca Whirlpool (example)
    PUMP_FUN_PROGRAM_ID,
]

MINT_RE = re.compile(r"(base_mint|mint|token)=([A-Za-z0-9]{32,44})", re.IGNORECASE)
POOL_RE = re.compile(r"(pool|amm|whirlpool)=([A-Za-z0-9]{32,44})", re.IGNORECASE)
GENERIC_PUBKEY_RE = re.compile(r"\b[A-Za-z0-9]{32,44}\b")


def parse_helius_logs(logs: list[str], program_ids: list[str]) -> dict | None:
    base_mint = None
    pool = None

    for line in logs:
        m = MINT_RE.search(line)
        if m and not base_mint:
            base_mint = m.group(2)

        p = POOL_RE.search(line)
        if p and not pool:
            pool = p.group(2)

    if not base_mint:
        for line in logs:
            for match in GENERIC_PUBKEY_RE.findall(line):
                base_mint = match
                break
            if base_mint:
                break

    if not base_mint:
        return None

    return {
        "token": base_mint,
        "pool": pool,
    }


async def listen(on_new_pool):
    async with websockets.connect(HELIUS_WS) as ws:
        sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": PROGRAM_IDS},
                {"commitment": "confirmed"},
            ],
        }
        await ws.send(json.dumps(sub))

        while True:
            msg = json.loads(await ws.recv())
            value = msg.get("params", {}).get("result", {}).get("value", {})
            logs = value.get("logs", [])

            print(
                f"[helius] logs received ({len(logs)} lines)",
                flush=True,
            )

            parsed = parse_helius_logs(logs, PROGRAM_IDS)
            if not parsed:
                continue

            print(
                f"[helius] parsed new pool token={parsed['token']}",
                flush=True,
            )

            event = {
                "source": "helius_pumpfun" if PUMP_FUN_PROGRAM_ID in PROGRAM_IDS else "helius",
                "type": "new_pool",
                "token": parsed["token"],
                "pool": parsed.get("pool"),
                "signature": value.get("signature"),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "logs": logs,
            }

            await on_new_pool(event)

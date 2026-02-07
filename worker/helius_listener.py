import os
import json
import asyncio
import websockets
from datetime import datetime, timezone

HELIUS_KEY = os.getenv("HELIUS_API_KEY")
HELIUS_WS = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# Raydium + Orca program IDs
PROGRAM_IDS = [
    "RVKd61ztZW9L5GxF3XH9RZy5D3R1xYbC5nZ5qZpZr2D",  # Raydium AMM (example)
    "whirLb8k1ZrZg2KqYF9rXy2rZpZqv2X6kz5n",          # Orca Whirlpool (example)
]


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
            logs = (
                msg.get("params", {})
                .get("result", {})
                .get("value", {})
                .get("logs", [])
            )

            for line in logs:
                if "initialize" in line.lower():
                    event = {
                        "source": "helius",
                        "type": "new_pool",
                        "raw_log": line,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await on_new_pool(event)

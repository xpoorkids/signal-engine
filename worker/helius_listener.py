import os
import json
import asyncio
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

async def listen(on_new_pool):
    async with websockets.connect(HELIUS_WS) as ws:
        sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "transactionSubscribe",
            "params": [
                {"accountInclude": PROGRAM_IDS},
                {
                    "commitment": "confirmed",
                    "encoding": "jsonParsed",
                    "transactionDetails": "full",
                    "showRewards": False,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        await ws.send(json.dumps(sub))

        while True:
            msg = json.loads(await ws.recv())
            print("[helius] tx message received", flush=True)

            result = msg.get("params", {}).get("result", {})
            tx = result.get("transaction", {})
            message = tx.get("message", {})
            instructions = message.get("instructions", [])

            for ix in instructions:
                program_id = ix.get("programId")
                if program_id not in PROGRAM_IDS:
                    continue

                parsed = ix.get("parsed")
                if not parsed:
                    continue

                info = parsed.get("info", {})
                mint = info.get("mint") or info.get("baseMint") or info.get("tokenMint")
                if not mint:
                    continue

                event = {
                    "source": "helius",
                    "type": "new_pool",
                    "token": mint,
                    "program": program_id,
                    "signature": result.get("signature"),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }

                await on_new_pool(event)

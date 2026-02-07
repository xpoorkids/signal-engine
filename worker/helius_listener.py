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

def extract_mint_from_inner_instructions(tx: dict) -> str | None:
    """
    Extract Pump.fun mint by resolving account index from InitializeMint CPI.
    """
    message = tx.get("transaction", {}).get("message", {})
    meta = tx.get("meta", {})

    account_keys = message.get("accountKeys", [])
    inner_ixs = meta.get("innerInstructions", [])

    for inner in inner_ixs:
        for ix in inner.get("instructions", []):
            parsed = ix.get("parsed")
            if not parsed:
                continue

            # Token Program mint initialization
            if parsed.get("type") == "initializeMint":
                accounts = ix.get("accounts", [])
                if not accounts:
                    continue

                # FIRST account is the mint for InitializeMint
                mint_index = accounts[0]

                try:
                    key = account_keys[mint_index]
                    if isinstance(key, dict):
                        return key.get("pubkey")
                    return key
                except Exception:
                    continue

    return None

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
            tx = result.get("transaction")
            meta = result.get("meta")

            if not tx or not meta:
                continue

            mint = extract_mint_from_inner_instructions({
                "transaction": tx,
                "meta": meta
            })

            if not mint:
                continue

            event = {
                "source": "helius_pumpfun",
                "type": "new_mint",
                "token": mint,
                "signature": result.get("signature"),
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }

            await on_new_pool(event)

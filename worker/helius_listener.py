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
    try:
        message = tx.get("transaction", {}).get("message", {})
        meta = tx.get("meta", {})
    except Exception:
        return None

    account_keys = message.get("accountKeys", [])
    inner_ixs = meta.get("innerInstructions", [])

    for inner in inner_ixs:
        try:
            instructions = inner.get("instructions", [])
        except Exception:
            continue
        for ix in instructions:
            try:
                parsed = ix.get("parsed")
            except Exception:
                continue
            if not parsed:
                continue

            # Token Program mint initialization
            if parsed.get("type") == "initializeMint":
                try:
                    accounts = ix.get("accounts", [])
                    if not accounts:
                        continue

                    # FIRST account is the mint for InitializeMint
                    mint_index = accounts[0]

                    key = account_keys[mint_index]
                    if isinstance(key, dict):
                        return key.get("pubkey")
                    return key
                except Exception:
                    continue

    return None


def extract_new_mints_from_token_balances(tx: dict) -> list[str]:
    """
    Detect newly created mints by comparing pre/post token balances.
    Pump.fun mints always appear here first.
    """
    try:
        meta = tx.get("meta")
        if not meta:
            return []
        pre = meta.get("preTokenBalances") or []
        post = meta.get("postTokenBalances") or []
    except Exception:
        return []

    pre_mints = set()
    for b in pre:
        try:
            mint = b.get("mint")
        except Exception:
            continue
        if mint:
            pre_mints.add(mint)
    new_mints = []

    for b in post:
        try:
            mint = b.get("mint")
        except Exception:
            continue
        if mint and mint not in pre_mints:
            new_mints.append(mint)

    return new_mints

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

        ws_tx_count = 0
        while True:
            try:
                raw = await ws.recv()
                msg = json.loads(raw)
                print("[helius] tx message received", flush=True)
            except Exception as e:
                print("[helius] recv/parse failed:", e, flush=True)
                continue

            ws_tx_count += 1
            if ws_tx_count % 100 == 0:
                print(f"[ws] tx_seen={ws_tx_count}", flush=True)

            try:
                result = msg.get("params", {}).get("result", {})
                tx = {
                    "transaction": result.get("transaction"),
                    "meta": result.get("meta"),
                }
            except Exception as e:
                print("[helius] result parse failed:", e, flush=True)
                continue

            try:
                new_mints = extract_new_mints_from_token_balances(tx)
            except Exception as e:
                print("[pump] token balance parse error:", e, flush=True)
                continue

            # Pump.fun detection signals (best-effort; never raise)
            try:
                message = tx.get("transaction", {}).get("message", {})
                account_keys = message.get("accountKeys", []) or []
                is_pump_program = False
                for k in account_keys:
                    try:
                        key = k.get("pubkey") if isinstance(k, dict) else k
                    except Exception:
                        continue
                    if key == PUMP_FUN_PROGRAM_ID:
                        is_pump_program = True
                        break
            except Exception:
                is_pump_program = False

            has_token_balance_diff = bool(new_mints)
            mint = None
            if new_mints:
                mint = new_mints[0]
            if not mint:
                try:
                    mint = extract_mint_from_inner_instructions(tx)
                except Exception as e:
                    print("[pump] extract failed:", e, flush=True)
                    mint = None

            is_pump_tx = is_pump_program and has_token_balance_diff and bool(mint)

            if not is_pump_tx:
                if not is_pump_program:
                    print("[pump] skip: not pump.fun program", flush=True)
                elif not has_token_balance_diff:
                    print("[pump] skip: no token balance diff", flush=True)
                elif not mint:
                    print("[pump] skip: mint unresolved", flush=True)
            else:
                print("[pump] HIT mint", mint, flush=True)

            # Temporarily widen the net (WS-only proof): emit when pump program is seen
            if is_pump_program:
                try:
                    event = {
                        "source": "helius_pumpfun",
                        "type": "ws_pump_observed",
                        "token": mint,
                        "signature": result.get("signature"),
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "has_token_balance_diff": has_token_balance_diff,
                    }
                    await on_new_pool(event)
                except Exception as e:
                    print("[pump] ws-only emit failed:", e, flush=True)

            for mint in new_mints:
                try:
                    event = {
                        "source": "helius_pumpfun",
                        "type": "new_mint",
                        "token": mint,
                        "signature": result.get("signature"),
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }
                except Exception as e:
                    print("[pump] event build failed:", e, flush=True)
                    continue
                try:
                    await on_new_pool(event)
                except Exception as e:
                    print("[pump] handler failed:", e, flush=True)

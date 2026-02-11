import os
import json
import asyncio
import websockets
import time
import requests
from collections import deque
from datetime import datetime, timezone
from worker.config import (
    ENABLE_LOGS_SUB,
    ENABLE_LOGS_TX_LOOKUP,
    HELIUS_API_KEY,
    HELIUS_WS_URL,
    HELIUS_RPC_URL,
)
from worker.events import Event

HELIUS_KEY = HELIUS_API_KEY or os.getenv("HELIUS_API_KEY")
HELIUS_WS = (
    HELIUS_WS_URL
    or os.getenv("HELIUS_WS_URL")
    or f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
)
HELIUS_RPC = (
    HELIUS_RPC_URL
    or os.getenv("HELIUS_RPC_URL")
    or f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
)

_last_rpc_log_ts = 0.0


def _is_buy_log(logs: list[str]) -> bool:
    for line in logs:
        log_line = str(line).lower()
        if "instruction: buy" in log_line or "instruction: swap" in log_line or "buy" in log_line:
            return True
    return False


def _log_rpc_issue(msg: str) -> None:
    global _last_rpc_log_ts
    now = time.time()
    if now - _last_rpc_log_ts > 30:
        print(f"[rpc] {msg}", flush=True)
        _last_rpc_log_ts = now

# Pump.fun program ID (mainnet)
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_PROGRAM_IDS = {
    # Main Pump.fun program
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    # Pump.fun bonding curve / aux programs (seen in logs & innerInstructions)
    "pmpn6JtN6P1q7xvJ5tJqGJZ3dXz7Jp6o4KkZc8KZz9G",  # bonding / curve helper
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token (mint init happens here)
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # Associated Token Program
}

PUMP_TRADE_PROGRAM_IDS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pmpn6JtN6P1q7xvJ5tJqGJZ3dXz7Jp6o4KkZc8KZz9G",
}

RAYDIUM_PROGRAM_IDS = {
    "RVKd61ztZW9L5GxF3XH9RZy5D3R1xYbC5nZ5qZpZr2D",
}

# Raydium + Orca + Pump.fun program IDs
PROGRAM_IDS = [
    "RVKd61ztZW9L5GxF3XH9RZy5D3R1xYbC5nZ5qZpZr2D",  # Raydium AMM (example)
    "whirLb8k1ZrZg2KqYF9rXy2rZpZqv2X6kz5n",          # Orca Whirlpool (example)
    PUMP_FUN_PROGRAM_ID,
    *sorted(PUMP_TRADE_PROGRAM_IDS),
    *sorted(RAYDIUM_PROGRAM_IDS),
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


def extract_mints_from_token_balances(tx: dict) -> list[str]:
    """
    Extract all mints seen in postTokenBalances (used for trade detection).
    """
    try:
        meta = tx.get("meta")
        if not meta:
            return []
        post = meta.get("postTokenBalances") or []
    except Exception:
        return []

    out = []
    for b in post:
        try:
            mint = b.get("mint")
        except Exception:
            continue
        if mint and mint not in out:
            out.append(mint)
    return out


def extract_buyers_from_balance_deltas(tx: dict) -> list[tuple[str, str]]:
    """
    Detect buyers by token balance increases for (owner, mint).
    Returns list of (mint, owner) where post > pre.
    """
    try:
        meta = tx.get("meta") or {}
        pre = meta.get("preTokenBalances") or []
        post = meta.get("postTokenBalances") or []
    except Exception:
        return []

    pre_map: dict[tuple[str, str], float] = {}
    for b in pre:
        try:
            mint = b.get("mint")
            owner = b.get("owner")
            amt = b.get("uiTokenAmount", {}).get("uiAmount")
        except Exception:
            continue
        if mint and owner:
            try:
                pre_map[(owner, mint)] = float(amt or 0)
            except Exception:
                pre_map[(owner, mint)] = 0.0

    out: list[tuple[str, str]] = []
    for b in post:
        try:
            mint = b.get("mint")
            owner = b.get("owner")
            amt = b.get("uiTokenAmount", {}).get("uiAmount")
        except Exception:
            continue
        if not mint or not owner:
            continue
        try:
            post_amt = float(amt or 0)
        except Exception:
            post_amt = 0.0
        pre_amt = pre_map.get((owner, mint), 0.0)
        if post_amt > pre_amt:
            out.append((mint, owner))
    return out


def _first_signer(tx: dict) -> str | None:
    try:
        message = tx.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", []) or []
    except Exception:
        return None
    for k in account_keys:
        try:
            if isinstance(k, dict) and k.get("signer"):
                return k.get("pubkey")
        except Exception:
            continue
    if account_keys:
        first = account_keys[0]
        if isinstance(first, dict):
            return first.get("pubkey")
        if isinstance(first, str):
            return first
    return None


def _resolve_mint_from_sig(sig: str) -> str | None:
    if not sig or not HELIUS_RPC:
        _log_rpc_issue("missing_sig_or_rpc_url")
        return None
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                sig,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        r = requests.post(HELIUS_RPC, json=payload, timeout=8)
        if r.status_code >= 300:
            _log_rpc_issue(f"getTransaction status={r.status_code} body={r.text[:120]}")
            return None
        data = r.json()
        result = data.get("result")
        if not result:
            _log_rpc_issue("getTransaction result=null")
            return None
        tx = {"transaction": result.get("transaction"), "meta": result.get("meta")}
        new_mints = extract_new_mints_from_token_balances(tx)
        if new_mints:
            return new_mints[0]
        return extract_mint_from_inner_instructions(tx)
    except Exception:
        _log_rpc_issue("getTransaction exception")
        return None


def _resolve_mint_and_buyer_from_sig(sig: str) -> tuple[str | None, str | None]:
    if not sig or not HELIUS_RPC:
        _log_rpc_issue("missing_sig_or_rpc_url")
        return None, None
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                sig,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        r = requests.post(HELIUS_RPC, json=payload, timeout=8)
        if r.status_code >= 300:
            _log_rpc_issue(f"getTransaction status={r.status_code} body={r.text[:120]}")
            return None, None
        data = r.json()
        result = data.get("result")
        if not result:
            _log_rpc_issue("getTransaction result=null")
            return None, None
        tx = {"transaction": result.get("transaction"), "meta": result.get("meta")}
        new_mints = extract_new_mints_from_token_balances(tx)
        mint = new_mints[0] if new_mints else extract_mint_from_inner_instructions(tx)
        buyer = _first_signer(tx)
        return mint, buyer
    except Exception:
        _log_rpc_issue("getTransaction exception")
        return None, None


def _resolve_tx_from_sig(sig: str) -> dict | None:
    if not sig or not HELIUS_RPC:
        _log_rpc_issue("missing_sig_or_rpc_url")
        return None
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                sig,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        r = requests.post(HELIUS_RPC, json=payload, timeout=8)
        if r.status_code >= 300:
            _log_rpc_issue(f"getTransaction status={r.status_code} body={r.text[:120]}")
            return None
        data = r.json()
        result = data.get("result")
        if not result:
            _log_rpc_issue("getTransaction result=null")
            return None
        return {"transaction": result.get("transaction"), "meta": result.get("meta")}
    except Exception:
        _log_rpc_issue("getTransaction exception")
        return None

async def listen(q: asyncio.Queue) -> None:
    if not HELIUS_KEY:
        print("[helius] missing HELIUS_API_KEY", flush=True)
    tx_sub = {
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
    logs_sub = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "logsSubscribe",
        "params": [
            "all",
            {"commitment": "confirmed"},
        ],
    }

    ws_tx_count = 0
    early_count = 0
    log_count = 0
    emit_count = 0
    recent_logs = deque(maxlen=50)
    pending_log_signatures: dict[str, float] = {}
    resolved_log_signatures: dict[str, float] = {}
    recent_buy_signatures: dict[str, float] = {}
    recent_balance_signatures: dict[str, float] = {}
    last_lookup_ts = 0.0
    last_buy_lookup_ts = 0.0
    last_pending_check = 0.0
    last_report_ts = time.time()
    last_emit_ts = time.time()

    while True:
        try:
            async with websockets.connect(
                HELIUS_WS,
                ping_interval=20,
                ping_timeout=20,
                max_queue=None,
            ) as ws:
                print("[helius] connected", flush=True)
                await ws.send(json.dumps(tx_sub))
                if ENABLE_LOGS_SUB:
                    await ws.send(json.dumps(logs_sub))
                    print("[logs] subscribed to ALL logs (confirmed)", flush=True)

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception as e:
                        print("[helius] recv/parse failed:", e, flush=True)
                        continue

                    async def emit_event(e: Event) -> None:
                        nonlocal emit_count, last_emit_ts
                        try:
                            print(f"[event-emit] type={e.type} token={e.token}", flush=True)
                            await q.put(e)
                            emit_count += 1
                            last_emit_ts = time.time()
                        except Exception as e2:
                            print("[pump-logs] emit failed:", e2, flush=True)

                    method = msg.get("method")
                    now = time.time()
                    if now - last_report_ts > 30:
                        print(
                            f"[helius] summary tx_msgs={ws_tx_count} logs_msgs={log_count} emits={emit_count}",
                            flush=True,
                        )
                        ws_tx_count = 0
                        log_count = 0
                        emit_count = 0
                        last_report_ts = now
                        if now - last_emit_ts > 120:
                            print("[event-emit] none in last 120s", flush=True)

                    # Logs-only visibility for InitializeMint
                    if method == "logsNotification":
                        buyer = None
                        try:
                            result = msg.get("params", {}).get("result", {})
                            value = result.get("value", {}) if isinstance(result, dict) else {}
                            logs = value.get("logs", [])
                            recent_logs.append(logs)
                            log_count += 1
                            if log_count % 200 == 0:
                                print(f"[logs] received={log_count}", flush=True)
                            if logs:
                                sig = value.get("signature") or result.get("signature")
                                if sig and ENABLE_LOGS_TX_LOOKUP and _is_buy_log(logs):
                                    last_seen = recent_buy_signatures.get(sig)
                                    if not last_seen or now - last_seen > 300:
                                        if now - last_buy_lookup_ts > 0.2:
                                            last_buy_lookup_ts = now
                                            mint, buyer = _resolve_mint_and_buyer_from_sig(sig)
                                            if mint and buyer:
                                                recent_buy_signatures[sig] = now
                                                print(
                                                    f"[buyer-detected] token={mint} wallet={buyer}",
                                                    flush=True,
                                                )
                                                await emit_event(
                                                    Event(
                                                        type="trade_buy",
                                                        source="logs",
                                                        signature=sig,
                                                        token=mint,
                                                        confidence=0.0,
                                                        reasons=["buy_detected_in_logs"],
                                                        extra={"buyer": buyer},
                                                    )
                                                )
                                for line in logs:
                                    log_line = str(line)
                                    if "InitializeMint" in log_line:
                                        early_count += 1
                                        print(f"[early] +1 via logs total={early_count}", flush=True)
                                        print("[FORCE] InitializeMint seen in logs", flush=True)
                                        print("[FORCE] logs InitializeMint EARLY emitted", flush=True)
                                        if sig:
                                            pending_log_signatures[sig] = time.time()
                                            if ENABLE_LOGS_TX_LOOKUP:
                                                now = time.time()
                                                if now - last_lookup_ts > 0.2 and sig not in resolved_log_signatures:
                                                    last_lookup_ts = now
                                                    mint = _resolve_mint_from_sig(sig)
                                                    if mint:
                                                        resolved_log_signatures[sig] = now
                                                        print(
                                                            f"[pump] token_resolved via logs lookup sig={sig} mint={mint}",
                                                            flush=True,
                                                        )
                                                        await emit_event(
                                                            Event(
                                                                type="token_resolved",
                                                                source="logs",
                                                                signature=sig,
                                                                token=mint,
                                                                confidence=0.55,
                                                                reasons=["mint_resolved_from_logs_lookup"],
                                                                extra={"buyer": buyer},
                                                            )
                                                        )
                                        await emit_event(
                                            Event(
                                                type="early_logs_initialize_mint",
                                                source="logs",
                                                signature=sig,
                                                confidence=0.4,
                                                reasons=["InitializeMint_in_logs"],
                                            )
                                        )
                                        break
                        except Exception as e:
                            print("[pump-logs] parse failed:", e, flush=True)

                        # Periodic retry for pending log signatures (processed logs may not be confirmed yet)
                        now = time.time()
                        if ENABLE_LOGS_TX_LOOKUP and now - last_pending_check > 1.0 and pending_log_signatures:
                            last_pending_check = now
                            if recent_buy_signatures:
                                stale = [s for s, ts in recent_buy_signatures.items() if now - ts > 900]
                                for s in stale:
                                    recent_buy_signatures.pop(s, None)
                            if recent_balance_signatures:
                                stale = [s for s, ts in recent_balance_signatures.items() if now - ts > 900]
                                for s in stale:
                                    recent_balance_signatures.pop(s, None)
                            for sig, first_seen in list(pending_log_signatures.items())[:5]:
                                if sig in resolved_log_signatures:
                                    pending_log_signatures.pop(sig, None)
                                    continue
                                if now - first_seen > 180:
                                    pending_log_signatures.pop(sig, None)
                                    continue
                                mint = _resolve_mint_from_sig(sig)
                                if mint:
                                    resolved_log_signatures[sig] = now
                                    pending_log_signatures.pop(sig, None)
                                    print(
                                        f"[pump] token_resolved via logs retry sig={sig} mint={mint}",
                                        flush=True,
                                    )
                                    await emit_event(
                                        Event(
                                            type="token_resolved",
                                            source="logs",
                                            signature=sig,
                                            token=mint,
                                            confidence=0.55,
                                            reasons=["mint_resolved_from_logs_retry"],
                                            extra={"buyer": buyer},
                                        )
                                    )
                        continue

                    if method and method != "transactionNotification":
                        continue

                    ws_tx_count += 1
                    if ws_tx_count % 10 == 0:
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

                    # Temporary visibility: program seen in account keys
                    try:
                        raw_keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
                        accounts = set(
                            k.get("pubkey") if isinstance(k, dict) else k for k in (raw_keys or [])
                        )
                        seen = accounts & PUMP_PROGRAM_IDS
                        if seen:
                            print("[pump] PROGRAM SEEN", list(seen), flush=True)
                    except Exception as e:
                        print("[pump] PROGRAM SEEN check failed:", e, flush=True)
                        accounts = set()

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

                    buyer = _first_signer(tx)

                    try:
                        logs = (tx.get("meta") or {}).get("logMessages") or []
                        if logs and _is_buy_log(logs):
                            trade_mints = extract_mints_from_token_balances(tx)
                            trade_mint = trade_mints[0] if trade_mints else mint
                            if trade_mint and buyer:
                                sig = result.get("signature")
                                print(
                                    f"[buyer-detected] token={trade_mint} wallet={buyer}",
                                    flush=True,
                                )
                                await emit_event(
                                    Event(
                                        type="trade_buy",
                                        source="tx",
                                        signature=sig,
                                        token=trade_mint,
                                        confidence=0.0,
                                        reasons=["buy_detected_in_tx_logs"],
                                        extra={"buyer": buyer},
                                    )
                                )
                    except Exception as e:
                        print("[buyer-detected] tx parse failed:", e, flush=True)

                    try:
                        sig = result.get("signature")
                        if sig:
                            last_seen = recent_balance_signatures.get(sig)
                            if not last_seen or now - last_seen > 300:
                                trade_accounts = accounts & (PUMP_TRADE_PROGRAM_IDS | RAYDIUM_PROGRAM_IDS)
                                if trade_accounts:
                                    full_tx = _resolve_tx_from_sig(sig)
                                    if full_tx:
                                        recent_balance_signatures[sig] = now
                                        for trade_mint, trade_buyer in extract_buyers_from_balance_deltas(full_tx):
                                            print(
                                                f"[buyer-detected] token={trade_mint} wallet={trade_buyer}",
                                                flush=True,
                                            )
                                            await emit_event(
                                                Event(
                                                    type="trade_buy",
                                                    source="tx_balance",
                                                    signature=sig,
                                                    token=trade_mint,
                                                    confidence=0.0,
                                                    reasons=["balance_increase_detected"],
                                                    extra={"buyer": trade_buyer},
                                                )
                                            )
                    except Exception as e:
                        print("[buyer-detected] balance delta failed:", e, flush=True)

                    # Temporarily widen the net (WS-only proof): emit when pump program is seen
                    if is_pump_program:
                        try:
                            sig = result.get("signature")
                            print(
                                f"[pump] tx program_seen sig={sig} mint={mint} balance_diff={has_token_balance_diff}",
                                flush=True,
                            )
                            await emit_event(
                                Event(
                                    type="early_tx_pump_observed",
                                    source="tx",
                                    signature=sig,
                                    program=PUMP_FUN_PROGRAM_ID,
                                    token=mint,
                                    confidence=0.35,
                                    reasons=["pump_program_seen_in_tx"],
                                    extra={
                                        "has_token_balance_diff": has_token_balance_diff,
                                        "observed_at": datetime.now(timezone.utc).isoformat(),
                                        "buyer": buyer,
                                    },
                                )
                            )
                        except Exception as e:
                            print("[pump] ws-only emit failed:", e, flush=True)

                    # Promote logs EARLY when token resolves on tx
                    try:
                        sig = result.get("signature")
                        if sig and sig in pending_log_signatures and mint:
                            print(f"[pump] token_resolved via logs sig={sig} mint={mint}", flush=True)
                            pending_log_signatures.pop(sig, None)
                            await emit_event(
                                Event(
                                    type="token_resolved",
                                    source="tx",
                                    signature=sig,
                                    token=mint,
                                    confidence=0.55,
                                    reasons=["mint_resolved_from_tx"],
                                    extra={"buyer": buyer},
                                )
                            )
                    except Exception as e:
                        print("[pump] logs promotion failed:", e, flush=True)

                    for mint in new_mints:
                        try:
                            sig = result.get("signature")
                            print(f"[pump] token_resolved via balances sig={sig} mint={mint}", flush=True)
                            await emit_event(
                                Event(
                                    type="token_resolved",
                                    source="tx",
                                    signature=sig,
                                    token=mint,
                                    confidence=0.55,
                                    reasons=["mint_resolved_from_tx"],
                                    extra={"buyer": buyer},
                                )
                            )
                        except Exception as e:
                            print("[pump] handler failed:", e, flush=True)

                    # Periodic retry for pending log signatures (processed logs may not be confirmed yet)
                    now = time.time()
                    if ENABLE_LOGS_TX_LOOKUP and now - last_pending_check > 1.0 and pending_log_signatures:
                        last_pending_check = now
                        if recent_buy_signatures:
                            stale = [s for s, ts in recent_buy_signatures.items() if now - ts > 900]
                            for s in stale:
                                recent_buy_signatures.pop(s, None)
                        if recent_balance_signatures:
                            stale = [s for s, ts in recent_balance_signatures.items() if now - ts > 900]
                            for s in stale:
                                recent_balance_signatures.pop(s, None)
                        for sig, first_seen in list(pending_log_signatures.items())[:5]:
                            if sig in resolved_log_signatures:
                                pending_log_signatures.pop(sig, None)
                                continue
                            if now - first_seen > 180:
                                pending_log_signatures.pop(sig, None)
                                continue
                            mint = _resolve_mint_from_sig(sig)
                            if mint:
                                resolved_log_signatures[sig] = now
                                pending_log_signatures.pop(sig, None)
                                print(
                                    f"[pump] token_resolved via logs retry sig={sig} mint={mint}",
                                    flush=True,
                                )
                                await emit_event(
                                    Event(
                                        type="token_resolved",
                                        source="logs",
                                        signature=sig,
                                        token=mint,
                                        confidence=0.55,
                                        reasons=["mint_resolved_from_logs_retry"],
                                    )
                                )
        except Exception as e:
            print("[helius] ws error, reconnecting:", e, flush=True)
            await asyncio.sleep(15)


async def start_helius_listeners(q: asyncio.Queue) -> None:
    print("[helius] starting listener", flush=True)
    try:
        await listen(q)
    except asyncio.CancelledError:
        print("[helius] listener cancelled", flush=True)
        raise
    except Exception as e:
        print("[helius] listener error:", e, flush=True)
    finally:
        print("[helius] listener stopped", flush=True)

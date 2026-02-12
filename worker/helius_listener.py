import os
import json
import asyncio
import websockets
import time
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from collections import deque, defaultdict
from datetime import datetime, timezone
from worker.config import (
    ENABLE_LOGS_SUB,
    ENABLE_LOGS_TX_LOOKUP,
    HELIUS_API_KEY,
    HELIUS_WS_URL,
    HELIUS_RPC_URL,
    PUMPFUN_PROGRAM_ID,
    RAYDIUM_AMM_PROGRAM_ID,
)
from worker.events import Event

HELIUS_KEY = HELIUS_API_KEY or os.getenv("HELIUS_API_KEY")
HELIUS_WS = (
    HELIUS_WS_URL
    or os.getenv("HELIUS_WS_URL")
    or f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
)


def _get_helius_rpc_url() -> str:
    url = (os.getenv("HELIUS_HTTPS_RPC_URL") or os.getenv("HELIUS_RPC_URL") or "").strip()
    api_key = (os.getenv("HELIUS_API_KEY") or "").strip()

    if not url:
        raise RuntimeError("Missing HELIUS_HTTPS_RPC_URL / HELIUS_RPC_URL env var")

    if "api-key=" in url or "apikey=" in url:
        return url

    if api_key:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}api-key={api_key}"

    return url


HELIUS_RPC = _get_helius_rpc_url()

_last_rpc_log_ts = 0.0

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
EXCLUDED_MINTS = {WSOL_MINT, USDC_MINT}

_LOG_SWAP_PATTERNS = [
    re.compile(r"instruction:\s*buy", re.IGNORECASE),
    re.compile(r"instruction:\s*swap", re.IGNORECASE),
    re.compile(r"raydium", re.IGNORECASE),
    re.compile(r"swap", re.IGNORECASE),
    re.compile(r"pump", re.IGNORECASE),
    re.compile(r"buy", re.IGNORECASE),
]

# signature-level dedup cache
_recent_buy_signatures: Dict[Tuple[str, str], float] = {}
DEDUP_TTL_SECONDS = 60
buy_size_method_counts = defaultdict(int)


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
PUMP_FUN_PROGRAM_ID = PUMPFUN_PROGRAM_ID
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
    RAYDIUM_AMM_PROGRAM_ID,
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


def _resolve_tx_from_sig(sig: str) -> Dict[str, Any] | None:
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
        return {"transaction": result.get("transaction"), "meta": result.get("meta"), "slot": result.get("slot")}
    except Exception:
        _log_rpc_issue("getTransaction exception")
        return None


def fetch_tx_with_retry(signature: str, max_attempts: int = 8, delay: float = 0.35) -> Optional[Dict[str, Any]]:
    for attempt in range(1, max_attempts + 1):
        try:
            tx = _resolve_tx_from_sig(signature)
            ok = 1 if tx else 0
            print(f"[tx-fetch] sig={signature} attempt={attempt} ok={ok}", flush=True)
            if tx:
                return tx
        except Exception as e:
            print(
                f"[tx-fetch] sig={signature} attempt={attempt} ok=0 err={type(e).__name__}:{e}",
                flush=True,
            )
        time.sleep(delay)
    print(f"[tx-missing-after-retry] sig={signature}", flush=True)
    return None


class LogSwapProcessor:
    def __init__(self, emit_event) -> None:
        self.emit_event = emit_event

    def _is_swap_log(self, logs: List[str]) -> bool:
        if not logs:
            return False
        low = [str(l).lower() for l in logs]
        has_instruction_buy = any("instruction: buy" in l for l in low)
        has_instruction_swap = any("instruction: swap" in l for l in low)
        has_raydium = any("raydium" in l for l in low)
        has_swap = any("swap" in l for l in low)
        has_pump = any("pump" in l for l in low)
        has_buy = any("buy" in l for l in low)

        if has_instruction_buy or has_instruction_swap:
            return True
        if has_raydium and has_swap:
            return True
        if has_pump and has_buy:
            return True
        return False

    def _token_balances_to_map(self, balances: List[Dict[str, Any]]) -> Dict[Tuple[str, str], int]:
        out: Dict[Tuple[str, str], int] = {}
        for b in balances or []:
            mint = b.get("mint")
            owner = b.get("owner") or b.get("accountOwner")
            ui = b.get("uiTokenAmount") or {}
            amt_str = ui.get("amount")
            if not mint or not owner or amt_str is None:
                continue
            try:
                raw = int(amt_str)
            except Exception:
                continue
            key = (owner, mint)
            out[key] = out.get(key, 0) + raw
        return out

    def _detect_positive_deltas(self, tx: Dict[str, Any]) -> List[Dict[str, Any]]:
        meta = (tx or {}).get("meta") or {}
        pre = meta.get("preTokenBalances") or []
        post = meta.get("postTokenBalances") or []

        pre_map = self._token_balances_to_map(pre)
        post_map = self._token_balances_to_map(post)

        decimals_by_key: Dict[Tuple[str, str], int] = {}
        for b in post + pre:
            mint = b.get("mint")
            owner = b.get("owner") or b.get("accountOwner")
            ui = b.get("uiTokenAmount") or {}
            dec = ui.get("decimals")
            if mint and owner and isinstance(dec, int):
                decimals_by_key[(owner, mint)] = dec

        keys = set(pre_map.keys()) | set(post_map.keys())
        hits: List[Dict[str, Any]] = []

        for (owner, mint) in keys:
            if mint in EXCLUDED_MINTS:
                continue
            pre_raw = pre_map.get((owner, mint), 0)
            post_raw = post_map.get((owner, mint), 0)
            delta_raw = post_raw - pre_raw
            if delta_raw > 0:
                hits.append(
                    {
                        "buyer": owner,
                        "mint": mint,
                        "delta_raw": delta_raw,
                        "decimals": decimals_by_key.get((owner, mint), 0),
                    }
                )
        return hits

    async def handle_logs_notification(self, notification: dict) -> None:
        try:
            value = (
                notification.get("params", {})
                .get("result", {})
                .get("value", {})
            )

            signature = value.get("signature")
            logs = value.get("logs") or []
            err = value.get("err")

            if not signature or err is not None:
                return

            if not self._is_swap_log(logs):
                return

            print(f"[log-swap-detected] sig={signature}", flush=True)

            await self._process_swap_signature(signature)
        except Exception as e:
            print(f"[logs-handler-error] {type(e).__name__}: {e}", flush=True)

    async def _process_swap_signature(self, signature: str) -> None:
        tx = fetch_tx_with_retry(signature)
        if not tx:
            return
        buys = self._detect_positive_deltas(tx)
        if not buys:
            return
        primary_mint = buys[0].get("mint") if buys else None
        buyer_signer, buyer_index, sol_spent, method, fee_lamports, signer_candidates, has_wsol_prepost, inner_transfer_sum = self._find_buyer_signer(
            signature, tx, primary_mint
        )
        for buy in buys:
            await self._emit_trade_buy(
                signature,
                tx,
                buy,
                buyer_signer,
                buyer_index,
                sol_spent,
                method,
                fee_lamports,
                signer_candidates,
                has_wsol_prepost,
                inner_transfer_sum,
            )

    def _find_buyer_signer(
        self,
        signature: str,
        tx: Dict[str, Any],
        mint: Optional[str],
    ) -> Tuple[Optional[str], Optional[int], float, str, int, List[str], int, int]:
        try:
            meta = tx.get("meta") or {}
            pre_balances = meta.get("preBalances") or []
            post_balances = meta.get("postBalances") or []
            fee = int(meta.get("fee") or 0)
            message = (tx.get("transaction") or {}).get("message") or {}
            keys = message.get("accountKeys") or []
        except Exception:
            print(
                f"[buy-size-miss] sig={signature} token={mint or ''} buyer=None signer_candidates=[] has_wsol_prepost=0 inner_transfer_sum=0",
                flush=True,
            )
            return None, None, 0.0, "fallback", 0, [], 0, 0

        best_owner = None
        best_index = None
        best_spent = 0
        signer_candidates: List[str] = []
        has_wsol_prepost = 0
        inner_transfer_sum = 0
        for idx, k in enumerate(keys):
            try:
                is_signer = k.get("signer") if isinstance(k, dict) else False
                is_writable = k.get("writable") if isinstance(k, dict) else False
                owner = k.get("pubkey") if isinstance(k, dict) else None
            except Exception:
                continue
            if not is_signer or not is_writable or owner is None:
                continue
            signer_candidates.append(owner)
            try:
                pre = int(pre_balances[idx])
                post = int(post_balances[idx])
            except Exception:
                continue
            lamports_spent = pre - post - fee
            if lamports_spent <= 0:
                continue
            if lamports_spent > best_spent:
                best_spent = lamports_spent
                best_owner = owner
                best_index = idx

        if best_spent > 0:
            return best_owner, best_index, best_spent / 1_000_000_000, "sol", fee, signer_candidates, has_wsol_prepost, inner_transfer_sum

        # WSOL fallback for signer
        try:
            pre_tokens = meta.get("preTokenBalances") or []
            post_tokens = meta.get("postTokenBalances") or []
            if best_owner:
                pre_wsol = 0
                post_wsol = 0
                for b in pre_tokens:
                    if b.get("mint") == WSOL_MINT and (b.get("owner") or b.get("accountOwner")) == best_owner:
                        pre_wsol = int((b.get("uiTokenAmount") or {}).get("amount") or 0)
                        has_wsol_prepost = 1
                        break
                for b in post_tokens:
                    if b.get("mint") == WSOL_MINT and (b.get("owner") or b.get("accountOwner")) == best_owner:
                        post_wsol = int((b.get("uiTokenAmount") or {}).get("amount") or 0)
                        has_wsol_prepost = 1
                        break
                if pre_wsol > post_wsol:
                    sol_spent = (pre_wsol - post_wsol) / 1_000_000_000
                    return best_owner, best_index, sol_spent, "wsol", fee, signer_candidates, has_wsol_prepost, inner_transfer_sum
        except Exception:
            pass

        # Inner system transfer fallback
        try:
            inner = meta.get("innerInstructions") or []
            buyer = best_owner
            if buyer:
                for entry in inner:
                    instructions = entry.get("instructions") or []
                    for ix in instructions:
                        program = ix.get("program")
                        program_id = ix.get("programId")
                        if program != "system" and program_id != "11111111111111111111111111111111":
                            continue
                        parsed = ix.get("parsed") or {}
                        if parsed.get("type") != "transfer":
                            continue
                        info = parsed.get("info") or {}
                        if info.get("source") != buyer:
                            continue
                        lamports = int(info.get("lamports") or 0)
                        if lamports > 0:
                            inner_transfer_sum += lamports
                if inner_transfer_sum > 0:
                    sol_spent = inner_transfer_sum / 1_000_000_000
                    return best_owner, best_index, sol_spent, "inner_sol_transfer", fee, signer_candidates, has_wsol_prepost, inner_transfer_sum
        except Exception:
            pass

        print(
            f"[buy-size-miss] sig={signature} token={mint or ''} "
            f"buyer={best_owner} signer_candidates={signer_candidates} has_wsol_prepost={has_wsol_prepost} inner_transfer_sum={inner_transfer_sum}",
            flush=True,
        )
        return best_owner, best_index, 0.0, "fallback", fee, signer_candidates, has_wsol_prepost, inner_transfer_sum

    async def _emit_trade_buy(
        self,
        signature: str,
        tx: Dict[str, Any],
        buy: Dict[str, Any],
        buyer_signer: Optional[str],
        buyer_index: Optional[int],
        sol_spent: float,
        method: str,
        fee_lamports: int,
        signer_candidates: List[str],
        has_wsol_prepost: int,
        inner_transfer_sum: int,
    ) -> None:
        now = time.time()
        mint = buy.get("mint")
        buyer = buyer_signer or buy.get("buyer")
        if not buyer or sol_spent is None or sol_spent <= 0 or method == "fallback":
            print(
                f"[buy-size-miss] sig={signature} token={mint} buyer={buyer} "
                f"signer_candidates={signer_candidates} has_wsol_prepost={has_wsol_prepost} inner_transfer_sum={inner_transfer_sum}",
                flush=True,
            )
            print(
                f"[skip-buy] reason=unknown_size sig={signature} token={mint} "
                f"buyer={buyer} method={method}",
                flush=True,
            )
            return
        dedup_key = (signature, mint)
        expired = [
            k for k, ts in _recent_buy_signatures.items()
            if now - ts > DEDUP_TTL_SECONDS
        ]
        for k in expired:
            del _recent_buy_signatures[k]
        if dedup_key in _recent_buy_signatures:
            print(f"[dedup-skip] sig={signature} token={mint}", flush=True)
            return
        _recent_buy_signatures[dedup_key] = now
        method_key = method or "fallback"
        if method_key == "inner_sol_transfer":
            method_key = "inner"
        buy_size_method_counts[method_key] += 1
        buy_size_method_counts["_total"] += 1
        if buy_size_method_counts["_total"] % 100 == 0:
            print(
                "[buy-size-stats] "
                f"sol={buy_size_method_counts['sol']} "
                f"wsol={buy_size_method_counts['wsol']} "
                f"inner={buy_size_method_counts['inner']} "
                f"fallback={buy_size_method_counts['fallback']}",
                flush=True,
            )
        print(
            f"[buy-size] token={mint} sig={signature} buyer={buyer} "
            f"sol={sol_spent} method={method}",
            flush=True,
        )
        event = Event(
            type="trade_buy",
            source="logs",
            signature=signature,
            token=mint,
            slot=tx.get("slot"),
            confidence=0.0,
            reasons=["balance_increase_detected"],
            extra={
                "buyer": buyer,
                "buyer_index": buyer_index,
                "fee_lamports": fee_lamports,
                "payer": buyer,
                "buy_size_sol": sol_spent,
                "sol_spent": sol_spent,
                "delta_raw": buy.get("delta_raw"),
                "decimals": buy.get("decimals"),
                "ts": int(time.time()),
            },
        )
        await self.emit_event(event)
        print("[event-emit] trade_buy", flush=True)

async def listen(q: asyncio.Queue) -> None:
    if not HELIUS_KEY:
        print("[helius] missing HELIUS_API_KEY", flush=True)
    print(f"[rpc-endpoint] url={HELIUS_RPC}", flush=True)
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
    recent_balance_signatures: dict[str, float] = {}
    last_lookup_ts = 0.0
    last_pending_check = 0.0
    last_report_ts = time.time()
    last_emit_ts = time.time()
    last_heartbeat_ts = 0.0
    reconnect_count = 0
    ws = None

    while True:
        try:
            print("[ws-connecting]", flush=True)
            async with websockets.connect(
                HELIUS_WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_queue=None,
            ) as ws:
                print("[ws-connected]", flush=True)
                if ENABLE_LOGS_SUB:
                    await ws.send(json.dumps(logs_sub))
                    print("[ws-subscribed]", flush=True)
                    if reconnect_count > 0:
                        print("[ws-reconnect-success]", flush=True)

                connection_start_time = time.time()

                while True:
                    now = time.time()
                    if now - connection_start_time > 240:
                        print("[ws-proactive-reconnect]", flush=True)
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        if now - last_heartbeat_ts >= 60:
                            print("[ws-heartbeat] connected=True", flush=True)
                            last_heartbeat_ts = now
                        continue

                    try:
                        msg = json.loads(raw)
                    except Exception as e:
                        print("[helius] recv/parse failed:", e, flush=True)
                        continue

                    if msg.get("id") == logs_sub.get("id"):
                        if "result" in msg:
                            print(
                                f"[ws-subscribe-response] id={msg.get('id')} result={msg.get('result')}",
                                flush=True,
                            )
                        elif "error" in msg:
                            err = msg.get("error") or {}
                            print(
                                f"[ws-subscribe-response] id={msg.get('id')} error_code={err.get('code')} "
                                f"error_message={err.get('message')} error={err}",
                                flush=True,
                            )

                    async def emit_event(e: Event) -> None:
                        nonlocal emit_count, last_emit_ts
                        try:
                            print(f"[event-emit] type={e.type} token={e.token}", flush=True)
                            await q.put(e)
                            emit_count += 1
                            last_emit_ts = time.time()
                        except Exception as e2:
                            print("[pump-logs] emit failed:", e2, flush=True)
                    swap_processor = LogSwapProcessor(emit_event)

                    method = msg.get("method")
                    now = time.time()
                    if now - last_heartbeat_ts >= 60:
                        print("[ws-heartbeat] connected=True", flush=True)
                        last_heartbeat_ts = now
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
                            sig = value.get("signature")
                            logs = value.get("logs", [])
                            recent_logs.append(logs)
                            log_count += 1
                            if log_count % 200 == 0:
                                print(f"[logs] received={log_count}", flush=True)
                            if logs:
                                await swap_processor.handle_logs_notification(msg)
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
                            print(f"[logs-handler-error] {type(e).__name__}: {e}", flush=True)

                        # Periodic retry for pending log signatures (processed logs may not be confirmed yet)
                        now = time.time()
                        if ENABLE_LOGS_TX_LOOKUP and now - last_pending_check > 1.0 and pending_log_signatures:
                            last_pending_check = now
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
                        continue

                    if method and method != "transactionNotification":
                        continue

                    ws_tx_count += 1
                    if ws_tx_count % 10 == 0:
                        print(f"[ws] tx_seen={ws_tx_count}", flush=True)

                    try:
                        result = msg.get("params", {}).get("result", {})
                        sig = result.get("signature")
                        if sig:
                            print(f"[tx-received] sig={sig}", flush=True)
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
                                    recent_balance_signatures[sig] = now
                                    for trade_mint, trade_buyer in extract_buyers_from_balance_deltas(tx):
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
            reconnect_count += 1
            print(f"[ws-error] {e} reconnect_count={reconnect_count}", flush=True)
        finally:
            try:
                if ws:
                    await ws.close()
            except Exception:
                pass

        await asyncio.sleep(1)


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

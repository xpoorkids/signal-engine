from __future__ import annotations

from typing import Any


PARSER_VERSION = "solana-trade-classifier-v1"
WSOL_MINT = "So11111111111111111111111111111111111111112"
STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4xTFe6uZzNrS2T9xTt4z": "USDT",
}


def classify_trade(tx: dict[str, Any], *, token: str) -> dict[str, Any]:
    pre = tx.get("pre_token_balances") or []
    post = tx.get("post_token_balances") or []
    token_delta = _owner_delta(pre, post, token)
    quote_delta = _owner_delta(pre, post, WSOL_MINT)
    stable_delta: dict[str, float] = {}
    stable_mint = None
    for mint in STABLE_MINTS:
        candidate = _owner_delta(pre, post, mint)
        if candidate:
            stable_delta = candidate
            stable_mint = mint
            break
    if not quote_delta and stable_delta:
        quote_delta = stable_delta
    side = "unknown"
    reasons: list[str] = []
    warnings: list[str] = []
    confidence = 0.2
    trader = None
    pool = _detect_pool(tx)
    venue = _detect_venue(tx)
    if token_delta:
        owner, delta = max(token_delta.items(), key=lambda item: abs(item[1]))
        trader = owner
        quote = quote_delta.get(owner, 0.0)
        if delta > 0 and quote < 0:
            side = "buy"
            confidence = 0.7
            reasons.append("token_increase_quote_decrease")
        elif delta < 0 and quote > 0:
            side = "sell"
            confidence = 0.7
            reasons.append("token_decrease_quote_increase")
        elif delta > 0:
            side = "transfer"
            reasons.append("token_increase_without_quote_match")
        elif delta < 0:
            side = "transfer"
            reasons.append("token_decrease_without_quote_match")
        if len([v for v in token_delta.values() if abs(v) > 0]) > 2:
            side = "routing" if side in {"buy", "sell"} else side
            confidence = min(confidence, 0.55)
            warnings.append("multiple_token_balance_changes")
    else:
        reasons.append("no_token_balance_delta")
        if _instruction_type_seen(tx, "mintTo"):
            side = "mint"
            confidence = 0.6
            reasons.append("mint_instruction_seen")
        elif _instruction_type_seen(tx, "burn"):
            side = "burn"
            confidence = 0.6
            reasons.append("burn_instruction_seen")
        elif _instruction_type_seen(tx, "initializeMint") or _instruction_type_seen(tx, "initializeMint2"):
            side = "pool_initialization" if pool else "mint"
            confidence = 0.5
            reasons.append("initialization_instruction_seen")
        elif _liquidity_log_seen(tx):
            side = "liquidity_add"
            confidence = 0.45
            reasons.append("liquidity_log_seen")
    return {
        "row_id": f"trade:{tx.get('signature')}:{token}:{side}:{trader or 'unknown'}",
        "chain": "solana",
        "token": token,
        "signature": tx.get("signature"),
        "slot": tx.get("slot"),
        "block_time": tx.get("block_time"),
        "token_mint": token,
        "quote_mint": stable_mint or (WSOL_MINT if quote_delta else None),
        "side": side,
        "trader": trader,
        "fee_payer": tx.get("fee_payer"),
        "signer": (tx.get("signers") or [None])[0],
        "pool": pool,
        "venue": venue,
        "dex_program": venue,
        "token_amount": abs(token_delta.get(trader, 0.0)) if trader else None,
        "quote_amount": abs(quote_delta.get(trader, 0.0)) if trader else None,
        "sol_equivalent": abs(quote_delta.get(trader, 0.0)) if trader else None,
        "usd_equivalent": None,
        "effective_execution_price": _price(token_delta.get(trader, 0.0), quote_delta.get(trader, 0.0)) if trader else None,
        "transaction_fee_lamports": tx.get("total_network_fee_lamports"),
        "success": tx.get("success"),
        "classification_confidence": confidence,
        "classification_reasons": reasons,
        "classification_warnings": warnings if warnings else ([] if confidence >= 0.7 else ["ambiguous_trade_classification"]),
        "parser_method": PARSER_VERSION,
        "source": tx.get("source"),
        "source_operation": tx.get("source_operation"),
        "observed_at": tx.get("observed_at"),
        "fetched_at": tx.get("fetched_at"),
        "evidence_quality": "reconstructed" if side in {"buy", "sell"} else "inferred",
        "parser_version": PARSER_VERSION,
        "job_id": tx.get("job_id"),
        "request_hash": tx.get("request_hash"),
        "response_hash": tx.get("response_hash"),
        "data_mode": tx.get("data_mode", "source"),
        "completeness": "usable" if side in {"buy", "sell"} else "partial",
        "warnings": warnings if warnings else ([] if side in {"buy", "sell"} else ["not_a_clear_swap"]),
    }


def _owner_delta(pre: list[dict[str, Any]], post: list[dict[str, Any]], mint: str) -> dict[str, float]:
    balances: dict[str, float] = {}
    for item in pre:
        if item.get("mint") == mint:
            owner = item.get("owner") or str(item.get("accountIndex"))
            balances[owner] = balances.get(owner, 0.0) - _amount(item)
    for item in post:
        if item.get("mint") == mint:
            owner = item.get("owner") or str(item.get("accountIndex"))
            balances[owner] = balances.get(owner, 0.0) + _amount(item)
    return {owner: delta for owner, delta in balances.items() if abs(delta) > 0}


def _amount(item: dict[str, Any]) -> float:
    amount = item.get("uiTokenAmount")
    if isinstance(amount, dict):
        return float(amount.get("uiAmount") or amount.get("uiAmountString") or 0)
    return float(item.get("uiAmount") or 0)


def _price(token_delta: float, quote_delta: float) -> float | None:
    if not token_delta:
        return None
    return abs(quote_delta / token_delta)


def _detect_pool(tx: dict[str, Any]) -> str | None:
    keys = tx.get("account_keys") or []
    programs = set(tx.get("program_ids") or [])
    for key in keys:
        text = str(key)
        if "pool" in text.lower() or text in programs:
            return text
    return None


def _detect_venue(tx: dict[str, Any]) -> str | None:
    programs = set(tx.get("program_ids") or [])
    known = {
        "6EF8rrecthR5DkJdS7rxBejfsBjgY6T5QYq6LL9pump": "pump_fun",
        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "pumpswap",
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "jupiter",
        "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "jupiter",
        "whirLbMiicVdio4qvUfM5KAg6CtQ5dqZFn1U74KjY8i": "orca",
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "meteora",
        "CPMMoo8L3F4NbTegBCKVNdioR1P6ZMXmG8t4P5zXQf6": "raydium_cpmm",
    }
    for program, venue in known.items():
        if program in programs:
            return venue
    return None


def _instruction_type_seen(tx: dict[str, Any], ix_type: str) -> bool:
    haystack = []
    for group in [tx.get("inner_instructions") or [], tx.get("log_messages") or []]:
        haystack.append(str(group))
    return ix_type.lower() in " ".join(haystack).lower()


def _liquidity_log_seen(tx: dict[str, Any]) -> bool:
    text = " ".join(str(item) for item in (tx.get("log_messages") or []))
    return "liquidity" in text.lower() or "initialize" in text.lower()

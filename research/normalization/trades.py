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
    side = "unknown"
    reasons: list[str] = []
    confidence = 0.2
    trader = None
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
    else:
        reasons.append("no_token_balance_delta")
    return {
        "row_id": f"trade:{tx.get('signature')}:{token}:{side}",
        "chain": "solana",
        "token": token,
        "signature": tx.get("signature"),
        "slot": tx.get("slot"),
        "block_time": tx.get("block_time"),
        "token_mint": token,
        "quote_mint": WSOL_MINT if quote_delta else None,
        "side": side,
        "trader": trader,
        "fee_payer": tx.get("fee_payer"),
        "signer": (tx.get("signers") or [None])[0],
        "pool": None,
        "dex_program": None,
        "token_amount": abs(token_delta.get(trader, 0.0)) if trader else None,
        "quote_amount": abs(quote_delta.get(trader, 0.0)) if trader else None,
        "sol_equivalent": abs(quote_delta.get(trader, 0.0)) if trader else None,
        "usd_equivalent": None,
        "effective_execution_price": _price(token_delta.get(trader, 0.0), quote_delta.get(trader, 0.0)) if trader else None,
        "transaction_fee_lamports": tx.get("total_network_fee_lamports"),
        "success": tx.get("success"),
        "classification_confidence": confidence,
        "classification_reasons": reasons,
        "classification_warnings": [] if confidence >= 0.7 else ["ambiguous_trade_classification"],
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
        "warnings": [] if side in {"buy", "sell"} else ["not_a_clear_swap"],
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


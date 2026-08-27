from __future__ import annotations

from typing import Any


PARSER_VERSION = "solana-transaction-normalizer-v1"


def normalize_transaction(record: dict[str, Any], *, token: str, source: str, job_id: str, request_hash: str | None, response_hash: str | None, data_mode: str = "source") -> dict[str, Any]:
    tx = record.get("transaction") if isinstance(record.get("transaction"), dict) else record
    meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else record.get("meta", {})
    transaction = tx.get("transaction") if isinstance(tx.get("transaction"), dict) else {}
    message = transaction.get("message") if isinstance(transaction.get("message"), dict) else tx.get("message", {})
    account_keys = message.get("accountKeys") or tx.get("accountKeys") or record.get("accountKeys") or []
    keys = [item.get("pubkey", item) if isinstance(item, dict) else item for item in account_keys]
    signers = [item.get("pubkey") for item in account_keys if isinstance(item, dict) and item.get("signer")]
    fee_payer = signers[0] if signers else (keys[0] if keys else record.get("feePayer"))
    signature = record.get("signature") or record.get("transactionSignature") or (transaction.get("signatures") or [None])[0]
    block_time = record.get("timestamp") or record.get("blockTime") or tx.get("blockTime")
    slot = record.get("slot") or tx.get("slot")
    fee = meta.get("fee", record.get("fee"))
    err = meta.get("err", record.get("err"))
    return {
        "row_id": f"{source}:{signature}",
        "chain": "solana",
        "token": token,
        "signature": signature,
        "slot": slot,
        "block_time": block_time,
        "success": err in (None, False, ""),
        "error": err,
        "fee_payer": fee_payer,
        "signers": signers,
        "account_keys": keys,
        "total_network_fee_lamports": fee,
        "base_fee_lamports": None,
        "priority_fee_lamports": None,
        "fee_split_status": "unavailable",
        "pre_balances": meta.get("preBalances"),
        "post_balances": meta.get("postBalances"),
        "pre_token_balances": meta.get("preTokenBalances"),
        "post_token_balances": meta.get("postTokenBalances"),
        "inner_instructions": meta.get("innerInstructions"),
        "log_messages": meta.get("logMessages"),
        "transaction_version": tx.get("version", record.get("version")),
        "source": source,
        "source_operation": record.get("source_operation", "transaction_history"),
        "observed_at": block_time,
        "fetched_at": record.get("fetched_at"),
        "evidence_quality": "parsed_direct",
        "parser_version": PARSER_VERSION,
        "job_id": job_id,
        "request_hash": request_hash,
        "response_hash": response_hash,
        "data_mode": data_mode,
        "completeness": "partial" if signature is None else "usable",
        "warnings": [] if signature else ["missing_signature"],
    }


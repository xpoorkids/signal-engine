from __future__ import annotations

from typing import Any


PARSER_VERSION = "solana-transaction-normalizer-v1"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"


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
    instructions = message.get("instructions") or tx.get("instructions") or []
    loaded_addresses = meta.get("loadedAddresses") or tx.get("loadedAddresses")
    program_ids = _program_ids(instructions, account_keys)
    cu_limit, cu_price = _compute_budget(instructions, account_keys)
    base_fee, priority_fee, split_status = _fee_split(fee, len(transaction.get("signatures") or []), cu_price, meta.get("computeUnitsConsumed"))
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
        "loaded_addresses": loaded_addresses,
        "program_ids": program_ids,
        "total_network_fee_lamports": fee,
        "base_fee_lamports": base_fee,
        "priority_fee_lamports": priority_fee,
        "fee_split_status": split_status,
        "number_of_signatures": len(transaction.get("signatures") or []),
        "compute_unit_limit": cu_limit,
        "compute_unit_price": cu_price,
        "prioritization_evidence": "compute_budget_instruction" if cu_price is not None else "unavailable",
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


def _program_ids(instructions: list[dict[str, Any]], account_keys: list[Any]) -> list[str]:
    keys = [item.get("pubkey", item) if isinstance(item, dict) else item for item in account_keys]
    out: list[str] = []
    for ix in instructions or []:
        program_id = ix.get("programId") if isinstance(ix, dict) else None
        if not program_id and isinstance(ix, dict) and ix.get("programIdIndex") is not None:
            try:
                program_id = keys[int(ix["programIdIndex"])]
            except Exception:
                program_id = None
        if program_id and program_id not in out:
            out.append(str(program_id))
    return out


def _compute_budget(instructions: list[dict[str, Any]], account_keys: list[Any]) -> tuple[int | None, int | None]:
    limit = None
    price = None
    keys = [item.get("pubkey", item) if isinstance(item, dict) else item for item in account_keys]
    for ix in instructions or []:
        if not isinstance(ix, dict):
            continue
        program_id = ix.get("programId")
        if not program_id and ix.get("programIdIndex") is not None:
            try:
                program_id = keys[int(ix["programIdIndex"])]
            except Exception:
                program_id = None
        if program_id != COMPUTE_BUDGET_PROGRAM:
            continue
        parsed = ix.get("parsed") if isinstance(ix.get("parsed"), dict) else {}
        info = parsed.get("info") if isinstance(parsed.get("info"), dict) else {}
        ix_type = parsed.get("type")
        if ix_type == "setComputeUnitLimit":
            limit = _int(info.get("units"))
        if ix_type == "setComputeUnitPrice":
            price = _int(info.get("microLamports"))
    return limit, price


def _fee_split(total_fee: Any, signature_count: int, compute_unit_price: int | None, compute_units_consumed: Any) -> tuple[int | None, int | None, str]:
    total = _int(total_fee)
    if total is None:
        return None, None, "unavailable"
    if compute_unit_price is None or compute_units_consumed is None:
        return None, None, "unavailable"
    base = max(0, signature_count) * 5000
    priority = int((int(compute_unit_price) * int(compute_units_consumed)) / 1_000_000)
    if base + priority <= total:
        return base, priority, "reconstructed_from_compute_budget"
    return None, None, "unavailable"


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None

from __future__ import annotations

from typing import Any


PARSER_VERSION = "solana-fee-normalizer-v1"


def normalize_fee(tx: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_id": f"fee:{tx.get('signature')}",
        "chain": "solana",
        "token": tx.get("token"),
        "signature": tx.get("signature"),
        "slot": tx.get("slot"),
        "block_time": tx.get("block_time"),
        "fee_payer": tx.get("fee_payer"),
        "buyer": None,
        "seller": None,
        "signers": tx.get("signers"),
        "total_network_fee_lamports": tx.get("total_network_fee_lamports"),
        "base_fee_lamports": tx.get("base_fee_lamports"),
        "priority_fee_lamports": tx.get("priority_fee_lamports"),
        "fee_split_status": tx.get("fee_split_status", "unavailable"),
        "transaction_success": tx.get("success"),
        "source": tx.get("source"),
        "source_operation": tx.get("source_operation"),
        "observed_at": tx.get("observed_at"),
        "fetched_at": tx.get("fetched_at"),
        "evidence_quality": tx.get("evidence_quality", "parsed_direct"),
        "parser_version": PARSER_VERSION,
        "job_id": tx.get("job_id"),
        "request_hash": tx.get("request_hash"),
        "response_hash": tx.get("response_hash"),
        "data_mode": tx.get("data_mode", "source"),
        "completeness": "usable" if tx.get("total_network_fee_lamports") is not None else "partial",
        "warnings": [] if tx.get("total_network_fee_lamports") is not None else ["fee_unavailable"],
    }


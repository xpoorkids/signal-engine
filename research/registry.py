from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from research.config import ResearchConfig
from research.models import EVM_HEX_ALPHABET, SOLANA_BASE58_ALPHABET
from research.storage import ResearchStore


OPERATOR_MANIFEST = Path(__file__).parent / "manifests" / "operator_seed_cohort_v1.yaml"
BENCHMARK_MANIFEST = Path(__file__).parent / "manifests" / "benchmark_candidate_names_v1.yaml"


def _read_simple_yaml_list(path: Path, key: str) -> list[str]:
    items: list[str] = []
    in_list = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == f"{key}:":
            in_list = True
            continue
        if in_list and stripped.startswith("- "):
            items.append(stripped[2:].strip())
        elif in_list and stripped and not stripped.startswith("#"):
            break
    return items


def load_operator_seed_addresses() -> list[str]:
    return _read_simple_yaml_list(OPERATOR_MANIFEST, "addresses")


def load_benchmark_candidate_names() -> list[str]:
    return _read_simple_yaml_list(BENCHMARK_MANIFEST, "names")


def detect_chain(address: str) -> str:
    value = str(address or "").strip()
    if value.startswith("0x") and len(value) == 42 and all(ch in EVM_HEX_ALPHABET for ch in value[2:]):
        return "evm"
    if 32 <= len(value) <= 48 and all(ch in SOLANA_BASE58_ALPHABET for ch in value):
        return "solana"
    return "invalid"


def token_id(chain: str, address: str) -> str:
    return hashlib.sha256(f"{chain}:{address}".encode("utf-8")).hexdigest()[:24]


def validate_operator_seeds(config: ResearchConfig) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    addresses = load_operator_seed_addresses()
    seen: set[str] = set()
    duplicates: list[str] = []
    results: list[dict[str, Any]] = []
    now = int(time.time())
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for address in addresses:
            if address in seen:
                duplicates.append(address)
                continue
            seen.add(address)
            chain = detect_chain(address)
            status = "valid_format" if chain in {"solana", "evm"} else "invalid_format"
            tid = token_id(chain, address)
            metadata = {
                "field_sources": {
                    "chain": "format_detector_v1",
                    "canonical_address": "operator_manifest",
                    "symbol": "unresolved_no_source",
                    "name": "unresolved_no_source",
                    "creation_ts": "unavailable_until_backfill",
                    "launchpad": "unavailable_until_backfill",
                    "traded_status": "unavailable_until_backfill",
                },
                "operator_label_is_not_ground_truth": True,
                "missing_fields_are_not_zero": True,
            }
            conn.execute(
                """
                INSERT INTO research_tokens (
                    token_id, supplied_address, canonical_chain, canonical_address, symbol, name,
                    source_label, operator_outcome_label, verification_status, validation_status,
                    creation_ts, launchpad, traded_status, metadata_json, created_ts, updated_ts
                ) VALUES (?, ?, ?, ?, NULL, NULL, 'operator_supplied', 'recent_winner', 'pending', ?, NULL, NULL, NULL, ?, ?, ?)
                ON CONFLICT(canonical_chain, canonical_address) DO UPDATE SET
                    supplied_address=excluded.supplied_address,
                    source_label=excluded.source_label,
                    operator_outcome_label=excluded.operator_outcome_label,
                    verification_status='pending',
                    validation_status=excluded.validation_status,
                    metadata_json=excluded.metadata_json,
                    updated_ts=excluded.updated_ts
                """,
                (tid, address, chain, address, status, json.dumps(metadata, sort_keys=True), now, now),
            )
            results.append(
                {
                    "supplied_address": address,
                    "token_id": tid,
                    "chain": chain,
                    "validation_status": status,
                    "source_label": "operator_supplied",
                    "operator_outcome_label": "recent_winner",
                    "verification_status": "pending",
                    "canonical_symbol": None,
                    "canonical_name": None,
                    "creation_time": None,
                    "launchpad_or_venue": None,
                    "traded_status": None,
                    "field_status": "identity_unresolved_until_source_backfill",
                }
            )
        conn.commit()
    return {
        "cohort_id": "operator_seed_cohort_v1",
        "count": len(results),
        "duplicates": duplicates,
        "solana_count": sum(1 for item in results if item["chain"] == "solana"),
        "evm_count": sum(1 for item in results if item["chain"] == "evm"),
        "invalid_count": sum(1 for item in results if item["chain"] == "invalid"),
        "results": results,
    }


def register_benchmark_names(config: ResearchConfig) -> dict[str, Any]:
    names = load_benchmark_candidate_names()
    return {
        "cohort_id": "benchmark_candidate_names_v1",
        "count": len(names),
        "identity_status": "candidate_names_only_not_token_identity",
        "names": names,
    }


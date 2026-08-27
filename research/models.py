from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SOLANA_BASE58_ALPHABET = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
EVM_HEX_ALPHABET = set("0123456789abcdefABCDEF")

FIELD_STATES = {
    "computed",
    "missing",
    "unavailable",
    "stale",
    "insufficient_history",
    "outside_source_retention",
    "unsupported_by_current_api_plan",
    "reference_price_only",
    "executable_estimate",
    "inferred",
    "directly_observed",
}

QUALITY_STATES = {"complete", "usable", "partial", "weak", "unavailable", "outside_retention"}
JOB_STATUSES = {"pending", "running", "partial", "completed", "source_unavailable", "failed", "dead_letter", "manually_skipped"}
EXECUTION_QUALITY = {
    "historical_quote_observed",
    "historical_reserve_reconstructed",
    "historical_trade_inferred",
    "historical_liquidity_estimated",
    "reference_price_only",
    "no_route",
    "insufficient_data",
}


@dataclass(frozen=True)
class FieldValue:
    value: Any = None
    state: str = "missing"
    source: str | None = None
    observed_at: int | None = None
    as_of_ts: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SeedToken:
    supplied_address: str
    source_label: str = "operator_supplied"
    operator_outcome_label: str = "recent_winner"
    verification_status: str = "pending"


@dataclass(frozen=True)
class SourceCapability:
    source: str
    enabled: bool
    api_key_configured: bool
    endpoint_available: bool
    plan_permits_endpoint: bool
    earliest_historical_time: str | None
    finest_available_interval: str | None
    rate_limit: str | None
    last_successful_request: int | None
    unavailable_reason: str | None
    fallback_source: str | None
    data_kind: str


@dataclass(frozen=True)
class ResearchSnapshot:
    token_id: str
    snapshot_ts: int
    label: str
    features: dict[str, FieldValue]
    source_hashes: list[str]


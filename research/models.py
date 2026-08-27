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

RESEARCH_MODES = {"source", "fixture", "hybrid"}
SOURCE_STATUSES = {
    "success",
    "partial",
    "empty",
    "not_configured",
    "unauthorized",
    "plan_restricted",
    "rate_limited",
    "outside_retention",
    "invalid_request",
    "source_unavailable",
    "malformed_response",
    "failed",
}
EVIDENCE_QUALITY = {
    "direct",
    "parsed_direct",
    "reconstructed",
    "inferred",
    "current_only",
    "reference_only",
    "unavailable",
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
class SourceResult:
    source: str
    operation: str
    status: str
    requested_start_ts: int | None = None
    requested_end_ts: int | None = None
    returned_start_ts: int | None = None
    returned_end_ts: int | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    completeness: str = "unavailable"
    retention_status: str = "unknown"
    evidence_quality: str = "unavailable"
    fetched_at: int | None = None
    request_hash: str | None = None
    response_hash: str | None = None
    parser_version: str = "source-result-v1"
    retry_count: int = 0
    rate_limit: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "operation": self.operation,
            "status": self.status,
            "requested_start_ts": self.requested_start_ts,
            "requested_end_ts": self.requested_end_ts,
            "returned_start_ts": self.returned_start_ts,
            "returned_end_ts": self.returned_end_ts,
            "records": self.records,
            "record_count": self.record_count,
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "completeness": self.completeness,
            "retention_status": self.retention_status,
            "evidence_quality": self.evidence_quality,
            "fetched_at": self.fetched_at,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "parser_version": self.parser_version,
            "retry_count": self.retry_count,
            "rate_limit": self.rate_limit,
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class ResearchSnapshot:
    token_id: str
    snapshot_ts: int
    label: str
    features: dict[str, FieldValue]
    source_hashes: list[str]

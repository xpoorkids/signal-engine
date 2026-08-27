from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from research.models import SourceResult


@dataclass(frozen=True)
class SourceResponse:
    source: str
    endpoint: str
    status: str
    payload: dict[str, Any]
    response_start_ts: int | None = None
    response_end_ts: int | None = None
    completeness_status: str = "unavailable"
    retryable: bool = False


class SourceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRequest:
    operation: str
    url: str | None = None
    method: str = "GET"
    params: dict[str, Any] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    requested_start_ts: int | None = None
    requested_end_ts: int | None = None


class HistoricalSourceAdapter:
    source = "unknown"

    def capability(self) -> dict[str, Any]:
        raise NotImplementedError

    def fetch_token_history(self, token: str, *, start_ts: int | None, end_ts: int | None, cursor: str | None = None) -> SourceResponse:
        raise SourceUnavailable(f"{self.source} historical fetch is not configured")


def unavailable_result(source: str, operation: str, reason: str, *, evidence_quality: str = "unavailable") -> SourceResult:
    return SourceResult(
        source=source,
        operation=operation,
        status="not_configured",
        completeness="unavailable",
        evidence_quality=evidence_quality,
        fetched_at=int(time.time()),
        warnings=[reason],
    )

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


class HistoricalSourceAdapter:
    source = "unknown"

    def capability(self) -> dict[str, Any]:
        raise NotImplementedError

    def fetch_token_history(self, token: str, *, start_ts: int | None, end_ts: int | None, cursor: str | None = None) -> SourceResponse:
        raise SourceUnavailable(f"{self.source} historical fetch is not configured")


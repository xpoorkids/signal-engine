from __future__ import annotations

import os
from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.models import SourceResult
from research.source_adapters.base import unavailable_result


PARSER_VERSION = "birdeye-history-adapter-v1"


class BirdeyeAdapter:
    source = "birdeye"
    base_url = "https://public-api.birdeye.so"

    def __init__(self, config: ResearchConfig, client: ResearchHttpClient | None = None, *, api_key: str | None = None):
        self.config = config
        self.client = client
        self.api_key = api_key or os.getenv("BIRDEYE_API_KEY", "").strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    def headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.api_key, "x-chain": "solana"}

    async def probe(self) -> dict[str, Any]:
        if not self.configured():
            return {"source": self.source, "operation": "token_overview", "status": "not_configured", "credential_configured": False}
        result = await self.token_overview("So11111111111111111111111111111111111111112")
        return {"source": self.source, "operation": "token_overview", "status": result.status, "credential_configured": True, "schema_valid": result.status in {"success", "empty"}}

    async def token_overview(self, token: str) -> SourceResult:
        return await self._get("token_overview", "/defi/token_overview", {"address": token}, evidence_quality="current_only")

    async def creation_info(self, token: str) -> SourceResult:
        return await self._get("creation_info", "/defi/token_creation_info", {"address": token}, evidence_quality="direct")

    async def ohlcv(self, token: str, *, start_ts: int, end_ts: int, interval: str = "1m") -> SourceResult:
        params = {"address": token, "type": interval, "time_from": start_ts, "time_to": end_ts}
        result = await self._get("ohlcv_v3", "/defi/ohlcv", params, evidence_quality="direct", requested_start_ts=start_ts, requested_end_ts=end_ts)
        records = _extract_items(result.records)
        returned = [int(row.get("unixTime") or row.get("time") or row.get("timestamp")) for row in records if row.get("unixTime") or row.get("time") or row.get("timestamp")]
        return SourceResult(
            source=self.source,
            operation="ohlcv_v3",
            status=result.status if records else ("empty" if result.status == "success" else result.status),
            requested_start_ts=start_ts,
            requested_end_ts=end_ts,
            returned_start_ts=min(returned, default=None),
            returned_end_ts=max(returned, default=None),
            records=sorted(records, key=lambda row: int(row.get("unixTime") or row.get("time") or row.get("timestamp") or 0)),
            completeness="partial" if returned and (min(returned) > start_ts or max(returned) < end_ts) else result.completeness,
            retention_status="measured_from_returned_coverage",
            evidence_quality="direct",
            fetched_at=result.fetched_at,
            request_hash=result.request_hash,
            response_hash=result.response_hash,
            parser_version=PARSER_VERSION,
            retry_count=result.retry_count,
            rate_limit=result.rate_limit,
            warnings=result.warnings + ([] if returned else ["outside_retention_or_empty"]),
            errors=result.errors,
        )

    async def token_trades(self, token: str, *, start_ts: int | None = None, end_ts: int | None = None, offset: int = 0, limit: int = 50) -> SourceResult:
        params: dict[str, Any] = {"address": token, "offset": offset, "limit": min(limit, 50)}
        if start_ts is not None:
            params["time_from"] = start_ts
        if end_ts is not None:
            params["time_to"] = end_ts
        return await self._get("token_trades", "/defi/txs/token", params, evidence_quality="parsed_direct", requested_start_ts=start_ts, requested_end_ts=end_ts)

    async def holder_distribution(self, token: str) -> SourceResult:
        return await self._get("holder_distribution", "/defi/v3/token/holder", {"address": token}, evidence_quality="current_only")

    async def security(self, token: str) -> SourceResult:
        return await self._get("token_security", "/defi/token_security", {"address": token}, evidence_quality="current_only")

    async def _get(
        self,
        operation: str,
        path: str,
        params: dict[str, Any],
        *,
        evidence_quality: str,
        requested_start_ts: int | None = None,
        requested_end_ts: int | None = None,
    ) -> SourceResult:
        if not self.configured() or not self.client:
            return unavailable_result(self.source, operation, "missing_env:BIRDEYE_API_KEY", evidence_quality=evidence_quality)
        return await self.client.request_json(source=self.source, operation=operation, method="GET", url=f"{self.base_url}{path}", params=params, headers=self.headers(), evidence_quality=evidence_quality, requested_start_ts=requested_start_ts, requested_end_ts=requested_end_ts)


def _extract_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for payload in records:
        if isinstance(payload, list):
            out.extend(payload)
        elif isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                out.extend(data["items"])
            elif isinstance(data, list):
                out.extend(data)
            elif isinstance(payload.get("items"), list):
                out.extend(payload["items"])
            else:
                out.append(payload)
    return [row for row in out if isinstance(row, dict)]


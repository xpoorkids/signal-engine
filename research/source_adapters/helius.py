from __future__ import annotations

import os
from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.models import SourceResult
from research.source_adapters.base import unavailable_result


PARSER_VERSION = "helius-history-adapter-v1"


class HeliusAdapter:
    source = "helius"
    base_url = "https://api.helius.xyz/v0"

    def __init__(self, config: ResearchConfig, client: ResearchHttpClient | None = None, *, api_key: str | None = None):
        self.config = config
        self.client = client
        self.api_key = api_key or os.getenv("HELIUS_API_KEY", "").strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    async def probe(self) -> dict[str, Any]:
        if not self.configured():
            return {"source": self.source, "operation": "getTransactionsForAddress", "status": "not_configured", "credential_configured": False}
        result = await self.get_transactions_for_address("11111111111111111111111111111111", limit=1)
        return {"source": self.source, "operation": "getTransactionsForAddress", "status": result.status, "credential_configured": True, "schema_valid": result.status in {"success", "empty", "invalid_request"}}

    async def get_transactions_for_address(
        self,
        address: str,
        *,
        before: str | None = None,
        until: str | None = None,
        limit: int = 100,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> SourceResult:
        if not self.configured() or not self.client:
            return unavailable_result(self.source, "getTransactionsForAddress", "missing_env:HELIUS_API_KEY")
        params: dict[str, Any] = {"api-key": self.api_key, "limit": min(limit, 100)}
        if before:
            params["before"] = before
        if until:
            params["until"] = until
        result = await self.client.request_json(source=self.source, operation="getTransactionsForAddress", method="GET", url=f"{self.base_url}/addresses/{address}/transactions", params=params, evidence_quality="parsed_direct", requested_start_ts=start_ts, requested_end_ts=end_ts)
        records = []
        for payload in result.records:
            if isinstance(payload, list):
                records.extend(payload)
            elif isinstance(payload, dict):
                records.append(payload)
        if start_ts or end_ts:
            filtered = []
            for record in records:
                ts = record.get("timestamp") or record.get("blockTime")
                if ts is None:
                    filtered.append(record)
                    continue
                if start_ts is not None and int(ts) < int(start_ts):
                    continue
                if end_ts is not None and int(ts) > int(end_ts):
                    continue
                filtered.append(record)
            records = filtered
        sorted_records = sorted(records, key=lambda row: int(row.get("timestamp") or row.get("blockTime") or 0))
        next_cursor = sorted_records[-1].get("signature") if sorted_records and len(sorted_records) >= min(limit, 100) else None
        return SourceResult(
            source=self.source,
            operation="getTransactionsForAddress",
            status=result.status if records else ("empty" if result.status == "success" else result.status),
            requested_start_ts=start_ts,
            requested_end_ts=end_ts,
            returned_start_ts=min((int(r.get("timestamp") or r.get("blockTime")) for r in records if r.get("timestamp") or r.get("blockTime")), default=None),
            returned_end_ts=max((int(r.get("timestamp") or r.get("blockTime")) for r in records if r.get("timestamp") or r.get("blockTime")), default=None),
            records=sorted_records,
            next_cursor=next_cursor,
            has_more=bool(next_cursor),
            completeness="partial" if next_cursor else result.completeness,
            retention_status="api_retention_unprobed",
            evidence_quality="parsed_direct",
            fetched_at=result.fetched_at,
            request_hash=result.request_hash,
            response_hash=result.response_hash,
            parser_version=PARSER_VERSION,
            retry_count=result.retry_count,
            rate_limit=result.rate_limit,
            warnings=result.warnings,
            errors=result.errors,
        )

from __future__ import annotations

import os
import time
from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.models import SourceResult
from research.source_adapters.base import unavailable_result


PARSER_VERSION = "helius-history-adapter-v1"
ENDPOINT_FAMILY = "helius_rpc_getTransactionsForAddress"
ENDPOINT_VERSION = "2026-08-27-docs"


class HeliusAdapter:
    source = "helius"
    base_url = "https://api.helius.xyz/v0"
    rpc_base_url = "https://mainnet.helius-rpc.com/"

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
        pagination_token: str | None = None,
        before: str | None = None,
        until: str | None = None,
        limit: int = 100,
        start_ts: int | None = None,
        end_ts: int | None = None,
        start_slot: int | None = None,
        end_slot: int | None = None,
    ) -> SourceResult:
        if not self.configured() or not self.client:
            return unavailable_result(self.source, "getTransactionsForAddress", "missing_env:HELIUS_API_KEY")
        filters: dict[str, Any] = {"status": "any", "tokenAccounts": "balanceChanged"}
        if start_ts is not None or end_ts is not None:
            filters["blockTime"] = {}
            if start_ts is not None:
                filters["blockTime"]["gte"] = int(start_ts)
            if end_ts is not None:
                filters["blockTime"]["lte"] = int(end_ts)
        if start_slot is not None or end_slot is not None:
            filters["slot"] = {}
            if start_slot is not None:
                filters["slot"]["gte"] = int(start_slot)
            if end_slot is not None:
                filters["slot"]["lte"] = int(end_slot)
        if before or until:
            filters["signature"] = {}
            if before:
                filters["signature"]["lt"] = before
            if until:
                filters["signature"]["gte"] = until
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransactionsForAddress",
            "params": [
                address,
                {
                    "transactionDetails": "full",
                    "sortOrder": "asc",
                    "limit": min(max(1, limit), 1000),
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                    "commitment": "finalized",
                    "filters": filters,
                    **({"paginationToken": pagination_token} if pagination_token else {}),
                },
            ],
        }
        result = await self.client.request_json(
            source=self.source,
            operation="getTransactionsForAddress",
            method="POST",
            url=f"{self.rpc_base_url}?api-key={self.api_key}",
            json_body=body,
            evidence_quality="parsed_direct",
            requested_start_ts=start_ts,
            requested_end_ts=end_ts,
        )
        records, source_cursor = _extract_helius_page(result.records)
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
        next_cursor = source_cursor
        if not next_cursor and sorted_records and len(sorted_records) >= min(max(1, limit), 1000):
            # Compatibility for the legacy enhanced REST shape, which used
            # `before=<last signature>` instead of the RPC pagination token.
            next_cursor = sorted_records[-1].get("signature")
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
            warnings=result.warnings + [f"endpoint_family:{ENDPOINT_FAMILY}", f"endpoint_version:{ENDPOINT_VERSION}"],
            errors=result.errors,
        )


async def collect_transactions_for_address(
    adapter: HeliusAdapter,
    address: str,
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
    start_slot: int | None = None,
    end_slot: int | None = None,
    request_budget: int | None = None,
    max_pages: int | None = None,
    max_records: int | None = None,
    resume_cursor: str | None = None,
) -> SourceResult:
    records_by_signature: dict[str, dict[str, Any]] = {}
    raw_hashes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    cursor = resume_cursor
    pages = 0
    stop_reason = "complete_to_requested_start"
    first_request_hash: str | None = None
    last_response_hash: str | None = None
    fetched_at = int(time.time())

    while True:
        if request_budget is not None and pages >= request_budget:
            stop_reason = "partial_request_budget"
            break
        if max_pages is not None and pages >= max_pages:
            stop_reason = "partial_page_limit"
            break
        if max_records is not None and len(records_by_signature) >= max_records:
            stop_reason = "partial_record_limit"
            break
        page = await adapter.get_transactions_for_address(
            address,
            pagination_token=cursor,
            limit=min(1000, max_records or 1000),
            start_ts=start_ts,
            end_ts=end_ts,
            start_slot=start_slot,
            end_slot=end_slot,
        )
        pages += 1
        first_request_hash = first_request_hash or page.request_hash
        last_response_hash = page.response_hash
        if page.response_hash:
            raw_hashes.append(page.response_hash)
        warnings.extend(page.warnings)
        errors.extend(page.errors)
        if page.status not in {"success", "empty"}:
            stop_reason = "partial_source_error" if records_by_signature else "unavailable"
            break
        for row in page.records:
            sig = row.get("signature") or ((row.get("transaction") or {}).get("signatures") or [None])[0]
            if not sig:
                continue
            enriched = dict(row)
            enriched["source_operation"] = "getTransactionsForAddress"
            enriched["endpoint_family"] = ENDPOINT_FAMILY
            enriched["endpoint_version"] = ENDPOINT_VERSION
            records_by_signature.setdefault(sig, enriched)
        cursor = page.next_cursor
        if not page.has_more or not cursor:
            stop_reason = "complete_to_requested_start"
            break

    records = sorted(records_by_signature.values(), key=lambda row: (int(row.get("blockTime") or row.get("timestamp") or 0), int(row.get("slot") or 0), str(row.get("signature") or "")))
    if max_records is not None:
        records = records[:max_records]
    times = [int(r.get("blockTime") or r.get("timestamp")) for r in records if r.get("blockTime") or r.get("timestamp")]
    slots = [int(r.get("slot")) for r in records if r.get("slot") is not None]
    if records:
        status = "partial" if stop_reason.startswith("partial") else "success"
    elif stop_reason == "empty":
        status = "empty"
    else:
        status = "source_unavailable"
    completeness = stop_reason if records else ("empty" if status == "empty" else "unavailable")
    return SourceResult(
        source=adapter.source,
        operation="collect_transactions_for_address",
        status=status,
        requested_start_ts=start_ts,
        requested_end_ts=end_ts,
        returned_start_ts=min(times, default=None),
        returned_end_ts=max(times, default=None),
        records=records,
        next_cursor=cursor,
        has_more=bool(cursor and stop_reason.startswith("partial")),
        completeness=completeness,
        retention_status="mainnet_unlimited_per_helius_docs_or_plan_limited",
        evidence_quality="parsed_direct",
        fetched_at=fetched_at,
        request_hash=first_request_hash,
        response_hash=last_response_hash,
        parser_version=PARSER_VERSION,
        rate_limit={"pages": pages, "raw_response_hashes": raw_hashes, "earliest_slot": min(slots, default=None), "latest_slot": max(slots, default=None), "stop_reason": stop_reason},
        warnings=warnings,
        errors=errors,
    )


def _extract_helius_page(payloads: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    records: list[dict[str, Any]] = []
    cursor: str | None = None
    for payload in payloads:
        if isinstance(payload, list):
            records.extend(row for row in payload if isinstance(row, dict))
            continue
        if not isinstance(payload, dict):
            continue
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        if isinstance(result.get("data"), list):
            records.extend(row for row in result["data"] if isinstance(row, dict))
            cursor = result.get("paginationToken") or result.get("nextCursor") or cursor
        elif "transaction" in result or "signature" in result:
            records.append(result)
            cursor = result.get("paginationToken") or cursor
    return records, cursor

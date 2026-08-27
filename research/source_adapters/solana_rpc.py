from __future__ import annotations

import os
import time
from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.models import SourceResult
from research.source_adapters.base import unavailable_result


CURRENT_ACCOUNT_STATE_GUARD = "current_account_state_cannot_be_used_as_historical_snapshot"
PARSER_VERSION = "solana-rpc-adapter-v1"
ENDPOINT_VERSION = "2026-08-27-solana-rpc-docs"


class SolanaRpcAdapter:
    source = "solana_rpc"

    def __init__(self, config: ResearchConfig, client: ResearchHttpClient | None = None, *, rpc_url: str | None = None):
        self.config = config
        self.rpc_url = rpc_url or os.getenv("HELIUS_RPC_URL", "").strip()
        self.client = client

    def configured(self) -> bool:
        return bool(self.rpc_url)

    async def probe(self) -> dict[str, Any]:
        if not self.configured() or not self.client:
            return {"source": self.source, "operation": "getHealth", "status": "not_configured", "credential_configured": False}
        result = await self.rpc("getHealth", [])
        return {"source": self.source, "operation": "getHealth", "status": result.status, "credential_configured": True, "schema_valid": bool(result.records)}

    async def rpc(self, method: str, params: list[Any] | None = None) -> SourceResult:
        if not self.configured() or not self.client:
            return unavailable_result(self.source, method, "missing_env:HELIUS_RPC_URL")
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        result = await self.client.request_json(source=self.source, operation=method, method="POST", url=self.rpc_url, json_body=body)
        if result.status == "success":
            payload = result.records[0]
            if isinstance(payload, dict) and payload.get("error"):
                return SourceResult(self.source, method, "failed", records=[], completeness="unavailable", evidence_quality="unavailable", fetched_at=int(time.time()), request_hash=result.request_hash, response_hash=result.response_hash, parser_version=PARSER_VERSION, retry_count=result.retry_count, errors=[str(payload["error"])])
            record = payload.get("result") if isinstance(payload, dict) else payload
            records = record if isinstance(record, list) else ([] if record is None else [record])
            return SourceResult(self.source, method, "empty" if not records else "success", records=records, completeness="complete", evidence_quality="direct", fetched_at=result.fetched_at, request_hash=result.request_hash, response_hash=result.response_hash, parser_version=PARSER_VERSION, retry_count=result.retry_count, rate_limit=result.rate_limit)
        return result

    async def get_account_info(self, address: str) -> SourceResult:
        return await self.rpc("getAccountInfo", [address, {"encoding": "jsonParsed", "commitment": "finalized"}])

    async def get_signatures_for_address(self, address: str, *, before: str | None = None, until: str | None = None, limit: int = 1000) -> SourceResult:
        opts: dict[str, Any] = {"limit": limit, "commitment": "finalized"}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        return await self.rpc("getSignaturesForAddress", [address, opts])

    async def get_transaction(self, signature: str) -> SourceResult:
        return await self.rpc("getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}])

    async def get_token_supply(self, mint: str) -> SourceResult:
        return await self.rpc("getTokenSupply", [mint, {"commitment": "finalized"}])

    async def get_token_largest_accounts(self, mint: str) -> SourceResult:
        return await self.rpc("getTokenLargestAccounts", [mint, {"commitment": "finalized"}])


def reject_current_account_state_for_historical_snapshot(snapshot_ts: int, observed_ts: int | None) -> None:
    if observed_ts is not None and abs(int(time.time()) - int(snapshot_ts)) > 300:
        raise ValueError(CURRENT_ACCOUNT_STATE_GUARD)


async def collect_signatures_for_address(
    adapter: SolanaRpcAdapter,
    address: str,
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
    until: str | None = None,
    request_budget: int | None = None,
    max_pages: int | None = None,
    max_records: int | None = None,
    resume_cursor: str | None = None,
) -> SourceResult:
    records_by_signature: dict[str, dict[str, Any]] = {}
    raw_hashes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    before = resume_cursor
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
        page = await adapter.get_signatures_for_address(address, before=before, until=until, limit=min(1000, max_records or 1000))
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
        if not page.records:
            stop_reason = "complete_to_requested_start" if records_by_signature else "empty"
            break
        reached_start = False
        for row in page.records:
            if not isinstance(row, dict):
                continue
            sig = row.get("signature")
            if not sig:
                continue
            bt = row.get("blockTime")
            if end_ts is not None and bt is not None and int(bt) > int(end_ts):
                continue
            if start_ts is not None and bt is not None and int(bt) < int(start_ts):
                reached_start = True
                continue
            enriched = dict(row)
            enriched["source_operation"] = "getSignaturesForAddress"
            enriched["endpoint_version"] = ENDPOINT_VERSION
            records_by_signature.setdefault(sig, enriched)
        before = page.records[-1].get("signature") if isinstance(page.records[-1], dict) else None
        if reached_start:
            stop_reason = "complete_to_requested_start"
            break
        if len(page.records) < min(1000, max_records or 1000) or not before:
            stop_reason = "complete_to_requested_start"
            break

    records = sorted(records_by_signature.values(), key=lambda row: (int(row.get("blockTime") or 0), int(row.get("slot") or 0), str(row.get("signature") or "")))
    if max_records is not None:
        records = records[:max_records]
    times = [int(r.get("blockTime")) for r in records if r.get("blockTime") is not None]
    if records:
        status = "partial" if stop_reason.startswith("partial") else "success"
    elif stop_reason == "empty":
        status = "empty"
    else:
        status = "source_unavailable"
    return SourceResult(
        source=adapter.source,
        operation="collect_signatures_for_address",
        status=status,
        requested_start_ts=start_ts,
        requested_end_ts=end_ts,
        returned_start_ts=min(times, default=None),
        returned_end_ts=max(times, default=None),
        records=records,
        next_cursor=before,
        has_more=bool(before and stop_reason.startswith("partial")),
        completeness=stop_reason if records else ("empty" if status == "empty" else "unavailable"),
        retention_status="rpc_node_archive_availability_dependent",
        evidence_quality="direct",
        fetched_at=fetched_at,
        request_hash=first_request_hash,
        response_hash=last_response_hash,
        parser_version=PARSER_VERSION,
        rate_limit={"pages": pages, "raw_response_hashes": raw_hashes, "stop_reason": stop_reason},
        warnings=warnings,
        errors=errors,
    )


async def hydrate_signatures(
    adapter: SolanaRpcAdapter,
    signatures: list[dict[str, Any]],
    *,
    request_budget: int | None = None,
    concurrency: int = 2,
    completed_signatures: set[str] | None = None,
) -> SourceResult:
    completed = completed_signatures or set()
    to_fetch = []
    for row in signatures:
        sig = row.get("signature") if isinstance(row, dict) else None
        if sig and sig not in completed and sig not in {item.get("signature") for item in to_fetch}:
            to_fetch.append(row)
    if request_budget is not None:
        to_fetch = to_fetch[:request_budget]
    sem = __import__("asyncio").Semaphore(max(1, concurrency))
    fetched_at = int(time.time())
    raw_hashes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    hydrated: list[dict[str, Any]] = []

    async def one(sig_row: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            signature = sig_row["signature"]
            result = await adapter.get_transaction(signature)
            if result.response_hash:
                raw_hashes.append(result.response_hash)
            warnings.extend(result.warnings)
            errors.extend(result.errors)
            if result.status == "success" and result.records:
                tx = dict(result.records[0])
                tx["signature"] = signature
                tx["hydration_state"] = "hydrated"
                tx["source_operation"] = "getTransaction"
                tx["request_hash"] = result.request_hash
                tx["response_hash"] = result.response_hash
                tx["fetched_at"] = result.fetched_at
                return tx
            return {
                "signature": signature,
                "slot": sig_row.get("slot"),
                "blockTime": sig_row.get("blockTime"),
                "err": sig_row.get("err"),
                "hydration_state": "null_result" if result.status == "empty" else result.status,
                "source_operation": "getTransaction",
                "request_hash": result.request_hash,
                "response_hash": result.response_hash,
                "fetched_at": result.fetched_at,
                "warnings": result.warnings,
                "errors": result.errors,
            }

    if to_fetch:
        hydrated = await __import__("asyncio").gather(*(one(row) for row in to_fetch))
    hydrated = sorted(hydrated, key=lambda row: (int(row.get("blockTime") or 0), int(row.get("slot") or 0), row.get("signature") or ""))
    states: dict[str, int] = {}
    for row in hydrated:
        state = str(row.get("hydration_state") or "unknown")
        states[state] = states.get(state, 0) + 1
    return SourceResult(
        source=adapter.source,
        operation="hydrate_signatures",
        status="success" if hydrated else "empty",
        records=hydrated,
        completeness="complete" if len(hydrated) == len(to_fetch) else "partial",
        retention_status="rpc_node_archive_availability_dependent",
        evidence_quality="direct",
        fetched_at=fetched_at,
        response_hash=raw_hashes[-1] if raw_hashes else None,
        parser_version=PARSER_VERSION,
        rate_limit={"requested": len(to_fetch), "hydration_states": states, "raw_response_hashes": raw_hashes},
        warnings=warnings,
        errors=errors,
    )

from __future__ import annotations

import os
from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.models import SourceResult
from research.source_adapters.base import unavailable_result


PARSER_VERSION = "birdeye-history-adapter-v1"
ENDPOINT_VERSION = "2026-08-27-birdeye-docs"
OHLCV_INTERVALS = ["1s", "15s", "30s", "1m", "5m", "15m", "1h"]


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
        params = {"address": token, "type": interval, "time_from": start_ts, "time_to": end_ts, "mode": "range", "padding": "false", "outlier": "false"}
        result = await self._get("ohlcv_v3", "/defi/v3/ohlcv", params, evidence_quality="direct", requested_start_ts=start_ts, requested_end_ts=end_ts)
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
            warnings=result.warnings + ([] if returned else ["outside_retention_or_empty"]) + [f"interval:{interval}", f"endpoint_version:{ENDPOINT_VERSION}"],
            errors=result.errors,
        )

    async def token_trades(self, token: str, *, start_ts: int | None = None, end_ts: int | None = None, offset: int = 0, limit: int = 50) -> SourceResult:
        params: dict[str, Any] = {"address": token, "offset": offset, "limit": min(limit, 50)}
        if start_ts is not None:
            params["time_from"] = start_ts
        if end_ts is not None:
            params["time_to"] = end_ts
        return await self._get("token_trades", "/defi/v3/token/txs", params, evidence_quality="parsed_direct", requested_start_ts=start_ts, requested_end_ts=end_ts)

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


async def collect_ohlcv_history(
    adapter: BirdeyeAdapter,
    token: str,
    *,
    start_ts: int,
    end_ts: int,
    intervals: list[str] | None = None,
    request_budget: int | None = None,
    max_pages: int | None = None,
) -> SourceResult:
    selected = intervals or OHLCV_INTERVALS
    all_rows: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    errors: list[str] = []
    raw_hashes: list[str] = []
    pages = 0
    chosen_interval: str | None = None
    stop_reason = "empty"
    first_request_hash: str | None = None
    last_response_hash: str | None = None
    # Segment each range to the documented 5,000-candle maximum.
    seconds_by_interval = {"1s": 1, "15s": 15, "30s": 30, "1m": 60, "5m": 300, "15m": 900, "1h": 3600}
    for interval in selected:
        step = seconds_by_interval.get(interval, 60) * 5000
        cursor_start = int(start_ts)
        interval_rows = 0
        while cursor_start < end_ts:
            if request_budget is not None and pages >= request_budget:
                stop_reason = "partial_request_budget"
                break
            if max_pages is not None and pages >= max_pages:
                stop_reason = "partial_page_limit"
                break
            cursor_end = min(int(end_ts), cursor_start + step)
            page = await adapter.ohlcv(token, start_ts=cursor_start, end_ts=cursor_end, interval=interval)
            pages += 1
            first_request_hash = first_request_hash or page.request_hash
            last_response_hash = page.response_hash
            if page.response_hash:
                raw_hashes.append(page.response_hash)
            warnings.extend(page.warnings)
            errors.extend(page.errors)
            if page.status in {"unauthorized", "plan_restricted", "not_configured", "invalid_request"}:
                stop_reason = page.status
                break
            for row in page.records:
                ts = _row_ts(row)
                if ts is None:
                    continue
                key = f"{token}:{interval}:{ts}:birdeye"
                normalized = normalize_candle(row, token=token, interval=interval, result=page)
                all_rows[key] = normalized
                interval_rows += 1
            cursor_start = cursor_end
        if interval_rows > 0:
            chosen_interval = interval
            stop_reason = "complete_to_requested_end" if cursor_start >= end_ts else stop_reason
            break
        if stop_reason in {"partial_request_budget", "partial_page_limit", "unauthorized", "plan_restricted", "not_configured"}:
            break
        warnings.append(f"interval_downgrade:{interval}")
    rows = sorted(all_rows.values(), key=lambda row: int(row.get("candle_start") or 0))
    times = [int(row["candle_start"]) for row in rows if row.get("candle_start") is not None]
    if rows:
        status = "success" if stop_reason == "complete_to_requested_end" else "partial"
    elif stop_reason in {"not_configured", "unauthorized", "plan_restricted", "invalid_request"}:
        status = stop_reason
    elif stop_reason == "partial_source_error":
        status = "source_unavailable"
    else:
        status = "empty"
    return SourceResult(
        source=adapter.source,
        operation="collect_ohlcv_history",
        status=status,
        requested_start_ts=start_ts,
        requested_end_ts=end_ts,
        returned_start_ts=min(times, default=None),
        returned_end_ts=max(times, default=None),
        records=rows,
        completeness=stop_reason if rows else "empty",
        retention_status="measured_by_interval_downgrade",
        evidence_quality="direct",
        fetched_at=rows[-1]["fetched_at"] if rows else None,
        request_hash=first_request_hash,
        response_hash=last_response_hash,
        parser_version=PARSER_VERSION,
        rate_limit={"pages": pages, "chosen_interval": chosen_interval, "raw_response_hashes": raw_hashes, "stop_reason": stop_reason},
        warnings=warnings,
        errors=errors,
    )


async def collect_token_trades(
    adapter: BirdeyeAdapter,
    token: str,
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
    request_budget: int | None = None,
    max_pages: int | None = None,
    max_records: int | None = None,
) -> SourceResult:
    offset = 0
    limit = 50
    pages = 0
    rows_by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    errors: list[str] = []
    raw_hashes: list[str] = []
    first_request_hash: str | None = None
    last_response_hash: str | None = None
    stop_reason = "complete_to_requested_end"
    while True:
        if request_budget is not None and pages >= request_budget:
            stop_reason = "partial_request_budget"
            break
        if max_pages is not None and pages >= max_pages:
            stop_reason = "partial_page_limit"
            break
        if max_records is not None and len(rows_by_id) >= max_records:
            stop_reason = "partial_record_limit"
            break
        page = await adapter.token_trades(token, start_ts=start_ts, end_ts=end_ts, offset=offset, limit=limit)
        pages += 1
        first_request_hash = first_request_hash or page.request_hash
        last_response_hash = page.response_hash
        if page.response_hash:
            raw_hashes.append(page.response_hash)
        warnings.extend(page.warnings)
        errors.extend(page.errors)
        if page.status not in {"success", "empty"}:
            stop_reason = "partial_source_error" if rows_by_id else page.status
            break
        items = _extract_items(page.records)
        if not items:
            break
        for index, row in enumerate(items):
            normalized = normalize_birdeye_trade(row, token=token, result=page, trade_index=offset + index)
            rows_by_id[normalized["row_id"]] = normalized
        if len(items) < limit:
            break
        offset += limit
    rows = sorted(rows_by_id.values(), key=lambda row: (int(row.get("block_time") or 0), int(row.get("trade_index") or 0)))
    if max_records is not None:
        rows = rows[:max_records]
    times = [int(row["block_time"]) for row in rows if row.get("block_time") is not None]
    if rows:
        status = "success" if stop_reason == "complete_to_requested_end" else "partial"
    elif stop_reason in {"not_configured", "unauthorized", "plan_restricted", "invalid_request"}:
        status = stop_reason
    elif stop_reason == "partial_source_error":
        status = "source_unavailable"
    else:
        status = "empty"
    return SourceResult(
        source=adapter.source,
        operation="collect_token_trades",
        status=status,
        requested_start_ts=start_ts,
        requested_end_ts=end_ts,
        returned_start_ts=min(times, default=None),
        returned_end_ts=max(times, default=None),
        records=rows,
        completeness=stop_reason if rows else "empty",
        retention_status="endpoint_plan_and_retention_dependent",
        evidence_quality="parsed_direct",
        fetched_at=rows[-1]["fetched_at"] if rows else None,
        request_hash=first_request_hash,
        response_hash=last_response_hash,
        parser_version=PARSER_VERSION,
        rate_limit={"pages": pages, "offset": offset, "raw_response_hashes": raw_hashes, "stop_reason": stop_reason},
        warnings=warnings,
        errors=errors,
    )


def normalize_candle(row: dict[str, Any], *, token: str, interval: str, result: SourceResult) -> dict[str, Any]:
    start = _row_ts(row)
    interval_seconds = {"1s": 1, "15s": 15, "30s": 30, "1m": 60, "5m": 300, "15m": 900, "1h": 3600}.get(interval, 60)
    return {
        "row_id": f"birdeye:{token}:{interval}:{start}",
        "chain": "solana",
        "token": token,
        "interval": interval,
        "candle_start": start,
        "candle_end": int(start or 0) + interval_seconds if start is not None else None,
        "open": _num(row.get("o", row.get("open"))),
        "high": _num(row.get("h", row.get("high"))),
        "low": _num(row.get("l", row.get("low"))),
        "close": _num(row.get("c", row.get("close"))),
        "volume": _num(row.get("v", row.get("volume"))),
        "liquidity_usd": _num(row.get("liquidity")),
        "source": result.source,
        "source_operation": result.operation,
        "observed_at": start,
        "fetched_at": result.fetched_at,
        "evidence_quality": result.evidence_quality,
        "parser_version": PARSER_VERSION,
        "job_id": None,
        "request_hash": result.request_hash,
        "response_hash": result.response_hash,
        "data_mode": "source",
        "completeness": result.completeness,
        "warnings": result.warnings,
    }


def normalize_birdeye_trade(row: dict[str, Any], *, token: str, result: SourceResult, trade_index: int) -> dict[str, Any]:
    sig = row.get("txHash") or row.get("signature") or row.get("tx_hash") or f"missing:{trade_index}"
    ts = row.get("blockUnixTime") or row.get("blockTime") or row.get("timestamp") or row.get("block_unix_time")
    side = str(row.get("side") or row.get("type") or row.get("txType") or "unknown").lower()
    return {
        "row_id": f"birdeye-trade:{token}:{sig}:{trade_index}",
        "chain": "solana",
        "token": token,
        "signature": sig,
        "trade_index": trade_index,
        "slot": row.get("slot"),
        "block_time": ts,
        "side": "buy" if "buy" in side else "sell" if "sell" in side else side,
        "trader": row.get("owner") or row.get("wallet") or row.get("trader"),
        "fee_payer": row.get("feePayer"),
        "signer": row.get("owner") or row.get("wallet") or row.get("trader"),
        "pool": row.get("poolId") or row.get("pairAddress") or row.get("address"),
        "venue": row.get("source") or row.get("dex") or row.get("platform"),
        "token_amount": _num(row.get("tokenAmount") or row.get("baseAmount") or row.get("amount")),
        "quote_amount": _num(row.get("quoteAmount") or row.get("quote_volume") or row.get("volume")),
        "sol_equivalent": _num(row.get("volumeNative") or row.get("volumeSol")),
        "usd_equivalent": _num(row.get("volumeUSD") or row.get("volumeUsd") or row.get("value")),
        "effective_execution_price": _num(row.get("price") or row.get("priceUsd")),
        "transaction_fee_lamports": row.get("fee"),
        "success": True,
        "classification_confidence": 0.75 if side in {"buy", "sell"} else 0.4,
        "classification_reasons": ["birdeye_v3_token_txs"],
        "classification_warnings": [] if side in {"buy", "sell"} else ["birdeye_side_ambiguous"],
        "parser_method": PARSER_VERSION,
        "source": result.source,
        "source_operation": result.operation,
        "observed_at": ts,
        "fetched_at": result.fetched_at,
        "evidence_quality": "parsed_direct",
        "parser_version": PARSER_VERSION,
        "job_id": None,
        "request_hash": result.request_hash,
        "response_hash": result.response_hash,
        "data_mode": "source",
        "completeness": result.completeness,
        "warnings": result.warnings,
    }


def _row_ts(row: dict[str, Any]) -> int | None:
    value = row.get("unixTime") or row.get("time") or row.get("timestamp") or row.get("blockUnixTime")
    return int(value) if value is not None else None


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

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

    async def get_signatures_for_address(self, address: str, *, before: str | None = None, limit: int = 1000) -> SourceResult:
        opts: dict[str, Any] = {"limit": limit, "commitment": "finalized"}
        if before:
            opts["before"] = before
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


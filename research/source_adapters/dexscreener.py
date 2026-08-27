from __future__ import annotations

from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.models import SourceResult


CURRENT_DEXSCREENER_GUARD = "current_dexscreener_liquidity_cannot_be_used_as_historical_snapshot"
PARSER_VERSION = "dexscreener-research-adapter-v1"


class DexScreenerAdapter:
    source = "dexscreener"
    base_url = "https://api.dexscreener.com"

    def __init__(self, config: ResearchConfig, client: ResearchHttpClient | None = None):
        self.config = config
        self.client = client

    async def probe(self) -> dict[str, Any]:
        if not self.client:
            return {"source": self.source, "operation": "token_profiles", "status": "not_configured", "credential_configured": True}
        result = await self.latest_token_profiles()
        return {"source": self.source, "operation": "token_profiles", "status": result.status, "credential_configured": True, "schema_valid": result.status in {"success", "empty"}}

    async def token_pairs(self, token: str) -> SourceResult:
        if not self.client:
            return SourceResult(self.source, "token_pairs", "not_configured", evidence_quality="unavailable")
        return await self.client.request_json(source=self.source, operation="token_pairs", method="GET", url=f"{self.base_url}/tokens/v1/solana/{token}", evidence_quality="current_only")

    async def pair_lookup(self, pair: str) -> SourceResult:
        if not self.client:
            return SourceResult(self.source, "pair_lookup", "not_configured", evidence_quality="unavailable")
        return await self.client.request_json(source=self.source, operation="pair_lookup", method="GET", url=f"{self.base_url}/latest/dex/pairs/solana/{pair}", evidence_quality="current_only")

    async def latest_token_profiles(self) -> SourceResult:
        if not self.client:
            return SourceResult(self.source, "token_profiles", "not_configured", evidence_quality="unavailable")
        return await self.client.request_json(source=self.source, operation="token_profiles", method="GET", url=f"{self.base_url}/token-profiles/latest/v1", evidence_quality="current_only")

    async def latest_boosts(self) -> SourceResult:
        if not self.client:
            return SourceResult(self.source, "latest_boosts", "not_configured", evidence_quality="unavailable")
        return await self.client.request_json(source=self.source, operation="latest_boosts", method="GET", url=f"{self.base_url}/token-boosts/latest/v1", evidence_quality="current_only")

    async def top_boosts(self) -> SourceResult:
        if not self.client:
            return SourceResult(self.source, "top_boosts", "not_configured", evidence_quality="unavailable")
        return await self.client.request_json(source=self.source, operation="top_boosts", method="GET", url=f"{self.base_url}/token-boosts/top/v1", evidence_quality="current_only")

    async def paid_orders(self, chain_id: str, token_address: str) -> SourceResult:
        if not self.client:
            return SourceResult(self.source, "paid_orders", "not_configured", evidence_quality="unavailable")
        return await self.client.request_json(source=self.source, operation="paid_orders", method="GET", url=f"{self.base_url}/orders/v1/{chain_id}/{token_address}", evidence_quality="current_only")

    @staticmethod
    def normalize_pairs(result: SourceResult, token: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for payload in result.records:
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict) and "pairAddress" in payload:
                items = [payload]
            elif isinstance(payload, dict):
                items = payload.get("pairs", [])
            else:
                items = []
            for pair in items:
                if not isinstance(pair, dict):
                    continue
                base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
                if base.get("address") != token:
                    continue
                rows.append(
                    {
                        "row_id": f"dexscreener:{pair.get('pairAddress')}",
                        "chain": "solana",
                        "token": token,
                        "pair_address": pair.get("pairAddress"),
                        "dex_id": pair.get("dexId"),
                        "base_token": base.get("address"),
                        "base_symbol": base.get("symbol"),
                        "base_name": base.get("name"),
                        "quote_token": (pair.get("quoteToken") or {}).get("address") if isinstance(pair.get("quoteToken"), dict) else None,
                        "pair_created_at": pair.get("pairCreatedAt"),
                        "price_usd": pair.get("priceUsd"),
                        "liquidity_usd": (pair.get("liquidity") or {}).get("usd") if isinstance(pair.get("liquidity"), dict) else None,
                        "market_cap": pair.get("marketCap"),
                        "fdv": pair.get("fdv"),
                        "volume": pair.get("volume"),
                        "txns": pair.get("txns"),
                        "source": "dexscreener",
                        "source_operation": result.operation,
                        "observed_at": result.fetched_at,
                        "fetched_at": result.fetched_at,
                        "evidence_quality": "current_only",
                        "parser_version": PARSER_VERSION,
                        "job_id": None,
                        "request_hash": result.request_hash,
                        "response_hash": result.response_hash,
                        "data_mode": "source",
                        "completeness": result.completeness,
                        "warnings": ["current_or_recent_context_not_historical_snapshot"],
                    }
                )
        return rows


def reject_current_dexscreener_for_historical_snapshot(snapshot_ts: int, observed_ts: int | None) -> None:
    if observed_ts is not None and abs(int(observed_ts) - int(snapshot_ts)) > 300:
        raise ValueError(CURRENT_DEXSCREENER_GUARD)

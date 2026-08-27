from __future__ import annotations

from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.models import SourceResult


PARSER_VERSION = "jupiter-current-only-adapter-v1"


class JupiterAdapter:
    source = "jupiter"
    price_url = "https://price.jup.ag/v6/price"
    quote_url = "https://quote-api.jup.ag/v6/quote"

    def __init__(self, config: ResearchConfig, client: ResearchHttpClient | None = None):
        self.config = config
        self.client = client

    async def probe(self) -> dict[str, Any]:
        if not self.client:
            return {"source": self.source, "operation": "current_price", "status": "not_configured", "credential_configured": False, "evidence_quality": "current_only"}
        result = await self.current_price("So11111111111111111111111111111111111111112")
        return {"source": self.source, "operation": "current_price", "status": result.status, "credential_configured": True, "evidence_quality": "current_only"}

    async def current_price(self, mint: str) -> SourceResult:
        if not self.client:
            return SourceResult(self.source, "current_price", "not_configured", evidence_quality="unavailable")
        return await self.client.request_json(source=self.source, operation="current_price", method="GET", url=self.price_url, params={"ids": mint}, evidence_quality="current_only")

    async def current_quote(self, input_mint: str, output_mint: str, amount: int) -> SourceResult:
        if not self.client:
            return SourceResult(self.source, "current_quote", "not_configured", evidence_quality="unavailable")
        return await self.client.request_json(source=self.source, operation="current_quote", method="GET", url=self.quote_url, params={"inputMint": input_mint, "outputMint": output_mint, "amount": amount}, evidence_quality="current_only")


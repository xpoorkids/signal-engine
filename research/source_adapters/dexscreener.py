from __future__ import annotations

from research.source_adapters.base import HistoricalSourceAdapter


class DexScreenerAdapter(HistoricalSourceAdapter):
    source = "dexscreener"

    def capability(self) -> dict:
        return {
            "source": self.source,
            "api_key_configured": True,
            "endpoint_available": True,
            "plan_permits_endpoint": True,
            "retention": "public_endpoint_retention_probe_required",
        }


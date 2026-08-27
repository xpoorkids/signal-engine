from __future__ import annotations

import os

from research.source_adapters.base import HistoricalSourceAdapter


class HeliusAdapter(HistoricalSourceAdapter):
    source = "helius"

    def capability(self) -> dict:
        configured = bool(os.getenv("HELIUS_API_KEY", "").strip())
        return {
            "source": self.source,
            "api_key_configured": configured,
            "endpoint_available": configured,
            "plan_permits_endpoint": False if not configured else None,
            "retention": "address_history_probe_required",
        }


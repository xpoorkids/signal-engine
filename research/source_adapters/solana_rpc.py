from __future__ import annotations

import os

from research.source_adapters.base import HistoricalSourceAdapter


class SolanaRpcAdapter(HistoricalSourceAdapter):
    source = "solana_rpc"

    def capability(self) -> dict:
        configured = bool(os.getenv("HELIUS_RPC_URL", "").strip() or os.getenv("SOLANA_RPC_URL", "").strip())
        return {
            "source": self.source,
            "api_key_configured": configured,
            "endpoint_available": configured,
            "plan_permits_endpoint": configured,
            "retention": "archive_node_dependent",
        }


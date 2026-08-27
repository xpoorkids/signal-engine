from __future__ import annotations

import json
import os
import time
from typing import Any

from research.config import ResearchConfig
from research.storage import ResearchStore


SOURCES = {
    "birdeye": {"env": "BIRDEYE_API_KEY", "fallback": "dexscreener,helius,solana_rpc", "kind": "direct_when_plan_permits"},
    "helius": {"env": "HELIUS_API_KEY", "fallback": "solana_rpc", "kind": "direct"},
    "dexscreener": {"env": None, "fallback": "birdeye", "kind": "direct_current_and_limited_historical"},
    "jupiter": {"env": "JUPITER_API_KEY", "fallback": None, "kind": "current_only_guarded"},
    "solana_rpc": {"env": "HELIUS_RPC_URL", "fallback": None, "kind": "direct_fallback"},
}


def probe_source_capabilities(config: ResearchConfig) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    checked = int(time.time())
    capabilities: list[dict[str, Any]] = []
    for source, meta in SOURCES.items():
        env_name = meta["env"]
        configured = bool(os.getenv(str(env_name), "").strip()) if env_name else True
        enabled = configured or source == "dexscreener"
        unavailable_reason = None
        endpoint_available = enabled
        plan_permits = configured or source in {"dexscreener", "solana_rpc"}
        earliest = None
        finest = None
        rate = None
        if not configured and env_name:
            unavailable_reason = f"missing_env:{env_name}"
            endpoint_available = False if source in {"birdeye", "helius", "jupiter"} else endpoint_available
            plan_permits = False if source in {"birdeye", "helius", "jupiter"} else plan_permits
        if source == "jupiter":
            earliest = "current_only_not_historical"
            finest = "current_quote"
        elif source == "dexscreener":
            earliest = "source_retention_probe_required"
            finest = "current_pair_snapshot_or_documented_history"
            rate = "public_rate_limit_unknown_probe_before_bulk"
        elif source == "birdeye":
            earliest = "plan_and_endpoint_retention_probe_required"
            finest = "endpoint_dependent"
        elif source == "helius":
            earliest = "address_history_retention_probe_required"
            finest = "transaction_or_slot"
        else:
            earliest = "rpc_archive_availability_dependent"
            finest = "slot"
        capabilities.append(
            {
                "source": source,
                "enabled": enabled,
                "api_key_configured": configured,
                "endpoint_available": endpoint_available,
                "plan_permits_endpoint": plan_permits,
                "earliest_historical_time": earliest,
                "finest_available_interval": finest,
                "rate_limit": rate,
                "last_successful_request": None,
                "unavailable_reason": unavailable_reason,
                "fallback_source": meta["fallback"],
                "data_kind": meta["kind"],
            }
        )
    with store.connect() as conn:
        for item in capabilities:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_source_capabilities (source, payload_json, checked_ts)
                VALUES (?, ?, ?)
                """,
                (item["source"], json.dumps(item, sort_keys=True), checked),
            )
    config.artifact_dir.mkdir(parents=True, exist_ok=True)
    (config.artifact_dir / "source_capabilities.json").write_text(json.dumps({"checked_ts": checked, "sources": capabilities}, indent=2), encoding="utf-8")
    (config.artifact_dir / "coverage_matrix.json").write_text(json.dumps({"checked_ts": checked, "coverage": capabilities}, indent=2), encoding="utf-8")
    return {"checked_ts": checked, "sources": capabilities}


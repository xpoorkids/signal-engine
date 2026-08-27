from __future__ import annotations

import json
import os
import time
import asyncio
from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.source_adapters.birdeye import BirdeyeAdapter
from research.source_adapters.dexscreener import DexScreenerAdapter
from research.source_adapters.helius import HeliusAdapter
from research.source_adapters.jupiter import JupiterAdapter
from research.source_adapters.solana_rpc import SolanaRpcAdapter
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
    live_probe = config.mode == "source"
    probe_rows = asyncio.run(_live_probe(config)) if live_probe else {}
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
                "operations": _operation_capabilities(source, configured, probe_rows.get(source)),
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


async def _live_probe(config: ResearchConfig) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    client = ResearchHttpClient(config)
    try:
        adapters = [
            HeliusAdapter(config, client),
            BirdeyeAdapter(config, client),
            DexScreenerAdapter(config, client),
            SolanaRpcAdapter(config, client),
            JupiterAdapter(config, client),
        ]
        for adapter in adapters:
            try:
                rows[adapter.source] = await adapter.probe()
            except Exception as exc:
                rows[adapter.source] = {"source": adapter.source, "status": "source_unavailable", "error_type": type(exc).__name__}
    finally:
        await client.aclose()
    return rows


def _operation_capabilities(source: str, configured: bool, probe: dict[str, Any] | None) -> list[dict[str, Any]]:
    status = (probe or {}).get("status")
    live_schema = (probe or {}).get("schema_valid")
    common = {
        "credential_configured": configured or source in {"dexscreener", "jupiter"},
        "authentication_accepted": status in {"success", "empty", "invalid_request"} if status else None,
        "endpoint_reachable": status not in {"source_unavailable", "not_configured"} if status else None,
        "response_schema_valid": live_schema,
        "rate_limit_headers": (probe or {}).get("rate_limit"),
        "request_cost": "unknown",
    }
    operations = {
        "helius": ["getTransactionsForAddress", "getSignaturesForAddress", "getTransaction", "getAccountInfo", "getTokenSupply"],
        "birdeye": ["creation_info", "token_overview", "ohlcv_v3", "token_trades", "holder_distribution", "token_security"],
        "dexscreener": ["token_pairs", "pair_lookup", "token_profiles", "boosts", "paid_orders"],
        "solana_rpc": ["getHealth", "getSlot", "getAccountInfo", "getSignaturesForAddress", "getTransaction", "getTokenSupply", "getTokenLargestAccounts"],
        "jupiter": ["current_price", "current_quote"],
    }[source]
    rows = []
    for op in operations:
        historical = source not in {"jupiter", "dexscreener"} and op not in {"holder_distribution", "token_security", "token_overview"}
        rows.append(
            {
                "operation": op,
                **common,
                "plan_permits_endpoint": False if not configured and source in {"helius", "birdeye"} else None if status is None else status not in {"plan_restricted", "unauthorized"},
                "historical_retention": "probe_required" if historical else "current_only",
                "finest_interval": "endpoint_dependent",
                "evidence_quality": "current_only" if not historical else "direct_or_parsed_direct",
                "fallback_method": SOURCES[source]["fallback"],
                "probe_status": status,
            }
        )
    return rows

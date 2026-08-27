from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from research.config import ResearchConfig
from research.http_client import ResearchHttpClient
from research.registry import detect_chain, token_id
from research.source_adapters.birdeye import BirdeyeAdapter
from research.source_adapters.dexscreener import DexScreenerAdapter
from research.source_adapters.solana_rpc import SolanaRpcAdapter
from research.storage import ResearchStore


IDENTITY_VERSION = "source-token-identity-v1"
RESOLUTION_STATES = {"verified", "partially_verified", "conflicting", "invalid", "wrong_chain", "unsupported", "source_unavailable"}


@dataclass
class IdentityResolution:
    supplied_address: str
    token_id: str
    chain: str
    status: str
    canonical_address: str
    token_program: str | None = None
    decimals: int | None = None
    supply: str | None = None
    symbol: str | None = None
    name: str | None = None
    creation_ts: int | None = None
    creation_signature: str | None = None
    creation_slot: int | None = None
    creation_source: str | None = None
    creation_confidence: str = "unavailable"
    first_activity_ts: int | None = None
    first_trade_ts: int | None = None
    first_pool_ts: int | None = None
    first_pair: str | None = None
    launchpad: str | None = None
    source_agreement: list[str] = field(default_factory=list)
    source_disagreement: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__ | {"identity_version": IDENTITY_VERSION, "row_id": f"identity:{self.chain}:{self.canonical_address}"}


def resolve_token_identity(config: ResearchConfig, address: str) -> IdentityResolution:
    return asyncio.run(resolve_token_identity_async(config, address))


async def resolve_token_identity_async(config: ResearchConfig, address: str) -> IdentityResolution:
    chain = detect_chain(address)
    tid = token_id(chain, address)
    if chain != "solana":
        status = "wrong_chain" if chain == "evm" else "invalid"
        resolution = IdentityResolution(address, tid, chain, status, address, warnings=["not_a_solana_mint"])
        _persist_identity(config, resolution)
        return resolution
    async with _client(config) as client:
        rpc = SolanaRpcAdapter(config, client)
        bird = BirdeyeAdapter(config, client)
        dex = DexScreenerAdapter(config, client)
        resolution = IdentityResolution(address, tid, chain, "source_unavailable", address)

        account = await rpc.get_account_info(address)
        if account.status == "success" and account.records:
            parsed = _extract_account_parsed(account.records[0])
            owner = _extract_account_owner(account.records[0])
            resolution.token_program = owner
            if parsed.get("type") == "mint":
                resolution.status = "verified"
                info = parsed.get("info") or {}
                resolution.decimals = info.get("decimals")
                resolution.supply = str(info.get("supply")) if info.get("supply") is not None else None
                resolution.source_agreement.append("solana_rpc:getAccountInfo:mint")
            else:
                resolution.status = "invalid"
                resolution.warnings.append(f"account_type_not_mint:{parsed.get('type')}")
        elif account.status != "not_configured":
            resolution.warnings.append(f"solana_rpc_account_info:{account.status}")

        supply = await rpc.get_token_supply(address)
        if supply.status == "success" and supply.records and isinstance(supply.records[0], dict):
            value = supply.records[0].get("value", supply.records[0])
            if isinstance(value, dict):
                resolution.decimals = resolution.decimals if resolution.decimals is not None else value.get("decimals")
                resolution.supply = resolution.supply or str(value.get("amount")) if value.get("amount") is not None else resolution.supply
                resolution.source_agreement.append("solana_rpc:getTokenSupply")

        creation = await bird.creation_info(address)
        if creation.status == "success" and creation.records:
            info = _first_data_dict(creation.records)
            resolution.creation_ts = _first_int(info, "blockUnixTime", "creationTime", "timestamp", "blockTime")
            resolution.creation_signature = info.get("txHash") or info.get("signature")
            resolution.creation_slot = _first_int(info, "slot")
            resolution.creation_source = "birdeye:creation_info"
            resolution.creation_confidence = "direct"
            resolution.source_agreement.append("birdeye:creation_info")
        elif creation.status not in {"not_configured", "empty"}:
            resolution.warnings.append(f"birdeye_creation:{creation.status}")

        overview = await bird.token_overview(address)
        if overview.status == "success" and overview.records:
            info = _first_data_dict(overview.records)
            resolution.symbol = info.get("symbol") or resolution.symbol
            resolution.name = info.get("name") or resolution.name
            resolution.source_agreement.append("birdeye:token_overview")

        pairs = await dex.token_pairs(address)
        pair_rows = DexScreenerAdapter.normalize_pairs(pairs, address)
        if pair_rows:
            first = sorted(pair_rows, key=lambda row: int(row.get("pair_created_at") or 0))[0]
            resolution.first_pair = first.get("pair_address")
            pair_created_ms = first.get("pair_created_at")
            resolution.first_pool_ts = int(pair_created_ms / 1000) if isinstance(pair_created_ms, (int, float)) and pair_created_ms > 10_000_000_000 else pair_created_ms
            resolution.symbol = resolution.symbol or first.get("base_symbol")
            resolution.name = resolution.name or first.get("base_name")
            resolution.launchpad = first.get("dex_id")
            resolution.source_agreement.append("dexscreener:token_pairs:current_context")
        elif pairs.status not in {"not_configured", "empty"}:
            resolution.warnings.append(f"dexscreener_pairs:{pairs.status}")

        if resolution.status == "source_unavailable" and resolution.source_agreement:
            resolution.status = "partially_verified"
        if resolution.status == "source_unavailable":
            resolution.warnings.append("no_source_verified_mint")
        _persist_identity(config, resolution)
        return resolution


class _client:
    def __init__(self, config: ResearchConfig):
        self.client = ResearchHttpClient(config)

    async def __aenter__(self) -> ResearchHttpClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.aclose()


def _persist_identity(config: ResearchConfig, resolution: IdentityResolution) -> None:
    store = ResearchStore(config)
    store.init_schema()
    now = int(time.time())
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO research_tokens (
                token_id, supplied_address, canonical_chain, canonical_address, symbol, name,
                source_label, operator_outcome_label, verification_status, validation_status,
                creation_ts, launchpad, traded_status, metadata_json, created_ts, updated_ts, data_mode
            ) VALUES (?, ?, ?, ?, ?, ?, 'operator_supplied', 'recent_winner', ?, 'valid_format', ?, ?, ?, ?, ?, ?, 'source')
            ON CONFLICT(canonical_chain, canonical_address) DO UPDATE SET
                symbol=excluded.symbol,
                name=excluded.name,
                verification_status=excluded.verification_status,
                creation_ts=excluded.creation_ts,
                launchpad=excluded.launchpad,
                traded_status=excluded.traded_status,
                metadata_json=excluded.metadata_json,
                updated_ts=excluded.updated_ts,
                data_mode='source'
            """,
            (
                resolution.token_id,
                resolution.supplied_address,
                resolution.chain,
                resolution.canonical_address,
                resolution.symbol,
                resolution.name,
                resolution.status,
                resolution.creation_ts,
                resolution.launchpad,
                "unknown" if resolution.first_trade_ts is None else "traded",
                json.dumps(resolution.to_dict(), sort_keys=True),
                now,
                now,
            ),
        )


def _extract_account_parsed(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("value") if isinstance(record, dict) else None
    data = (value or {}).get("data") if isinstance(value, dict) else record.get("data") if isinstance(record, dict) else None
    return data.get("parsed", {}) if isinstance(data, dict) else {}


def _extract_account_owner(record: dict[str, Any]) -> str | None:
    value = record.get("value") if isinstance(record, dict) else None
    if isinstance(value, dict):
        return value.get("owner")
    return record.get("owner") if isinstance(record, dict) else None


def _first_data_dict(records: list[dict[str, Any]]) -> dict[str, Any]:
    payload = records[0]
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        return data
    return payload if isinstance(payload, dict) else {}


def _first_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if value is not None:
            try:
                return int(value)
            except Exception:
                return None
    return None

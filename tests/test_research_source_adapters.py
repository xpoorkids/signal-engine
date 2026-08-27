from __future__ import annotations

import asyncio

import httpx

from research.config import load_config
from research.http_client import ResearchHttpClient
from research.source_adapters.birdeye import BirdeyeAdapter
from research.source_adapters.dexscreener import DexScreenerAdapter
from research.source_adapters.helius import HeliusAdapter
from research.source_adapters.solana_rpc import SolanaRpcAdapter


def test_helius_pagination_cursor_from_last_signature(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"signature": "b", "timestamp": 2}, {"signature": "a", "timestamp": 1}])

    config = load_config(db_path=str(tmp_path / "r.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    client = ResearchHttpClient(config, transport=httpx.MockTransport(handler))
    adapter = HeliusAdapter(config, client, api_key="configured")
    try:
        result = asyncio.run(adapter.get_transactions_for_address("mint", limit=2))
    finally:
        asyncio.run(client.aclose())
    assert result.next_cursor == "b"
    assert [row["signature"] for row in result.records] == ["a", "b"]


def test_solana_rpc_extracts_result_records(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": [{"signature": "sig"}], "id": 1})

    config = load_config(db_path=str(tmp_path / "r.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    client = ResearchHttpClient(config, transport=httpx.MockTransport(handler))
    adapter = SolanaRpcAdapter(config, client, rpc_url="https://rpc.example")
    try:
        result = asyncio.run(adapter.get_signatures_for_address("mint"))
    finally:
        asyncio.run(client.aclose())
    assert result.status == "success"
    assert result.records[0]["signature"] == "sig"


def test_birdeye_ohlcv_marks_partial_retention(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"items": [{"unixTime": 110, "c": 1.0}]}})

    config = load_config(db_path=str(tmp_path / "r.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    client = ResearchHttpClient(config, transport=httpx.MockTransport(handler))
    adapter = BirdeyeAdapter(config, client, api_key="configured")
    try:
        result = asyncio.run(adapter.ohlcv("mint", start_ts=100, end_ts=200))
    finally:
        asyncio.run(client.aclose())
    assert result.completeness == "partial"
    assert result.retention_status == "measured_from_returned_coverage"


def test_dexscreener_selects_correct_base_token(tmp_path) -> None:
    result = httpx.Response(200, json=[
        {"pairAddress": "wrong", "baseToken": {"address": "other"}},
        {"pairAddress": "right", "baseToken": {"address": "mint", "symbol": "M"}, "quoteToken": {"address": "SOL"}, "liquidity": {"usd": 10}},
    ])
    source_result = load_source_result(result.json())
    rows = DexScreenerAdapter.normalize_pairs(source_result, "mint")
    assert len(rows) == 1
    assert rows[0]["pair_address"] == "right"
    assert rows[0]["evidence_quality"] == "current_only"


def load_source_result(records):
    from research.models import SourceResult

    return SourceResult("dexscreener", "token_pairs", "success", records=records if isinstance(records, list) else [records], completeness="complete", evidence_quality="current_only", fetched_at=1)


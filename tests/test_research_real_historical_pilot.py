from __future__ import annotations

import asyncio
import json

import httpx

from research.config import load_config
from research.historical_builders import build_historical_outcomes, build_historical_snapshots
from research.http_client import ResearchHttpClient
from research.parquet_writer import write_parquet_table
from research.replay.action_replay import replay_token_snapshots, snapshot_market
from research.source_adapters.birdeye import BirdeyeAdapter, collect_ohlcv_history, collect_token_trades
from research.source_adapters.helius import HeliusAdapter, collect_transactions_for_address
from research.source_adapters.solana_rpc import SolanaRpcAdapter, collect_signatures_for_address, hydrate_signatures
from research.source_pipeline import _assert_no_fixture_source_totals
from research.storage import ResearchStore


def _source_config(tmp_path):
    return load_config(db_path=str(tmp_path / "r.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts")).__class__(
        db_path=tmp_path / "r.db",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "artifacts",
        mode="source",
        max_retries=0,
        request_budget=20,
        max_concurrency=2,
    )


def test_helius_collects_all_pages_and_persists_cursor_metadata(tmp_path) -> None:
    seen_tokens = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        cursor = body["params"][1].get("paginationToken")
        seen_tokens.append(cursor)
        if cursor is None:
            return httpx.Response(200, json={"result": {"data": [{"signature": "b", "blockTime": 20, "slot": 2}], "paginationToken": "2:1"}})
        return httpx.Response(200, json={"result": {"data": [{"signature": "a", "blockTime": 10, "slot": 1}], "paginationToken": None}})

    config = _source_config(tmp_path)
    client = ResearchHttpClient(config, transport=httpx.MockTransport(handler))
    adapter = HeliusAdapter(config, client, api_key="configured")
    try:
        result = asyncio.run(collect_transactions_for_address(adapter, "mint", max_pages=3))
    finally:
        asyncio.run(client.aclose())
    assert seen_tokens == [None, "2:1"]
    assert result.completeness == "complete_to_requested_start"
    assert [row["signature"] for row in result.records] == ["a", "b"]


def test_rpc_paginates_signatures_and_hydrates_transactions(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "getSignaturesForAddress":
            before = body["params"][1].get("before")
            records = [{"signature": "sig2", "slot": 2, "blockTime": 20}, {"signature": "sig1", "slot": 1, "blockTime": 10}] if before is None else []
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": records, "id": 1})
        if method == "getTransaction":
            sig = body["params"][0]
            if sig == "sig2":
                return httpx.Response(200, json={"jsonrpc": "2.0", "result": None, "id": 1})
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"slot": 1, "blockTime": 10, "meta": {"fee": 5000, "err": None}, "transaction": {"signatures": [sig], "message": {"accountKeys": [{"pubkey": "payer", "signer": True}]}}}, "id": 1})
        raise AssertionError(method)

    config = _source_config(tmp_path)
    client = ResearchHttpClient(config, transport=httpx.MockTransport(handler))
    adapter = SolanaRpcAdapter(config, client, rpc_url="https://rpc.example")
    try:
        sigs = asyncio.run(collect_signatures_for_address(adapter, "mint", max_pages=2))
        hydrated = asyncio.run(hydrate_signatures(adapter, sigs.records, concurrency=2))
    finally:
        asyncio.run(client.aclose())
    assert [row["signature"] for row in sigs.records] == ["sig1", "sig2"]
    assert {row["hydration_state"] for row in hydrated.records} == {"hydrated", "null_result"}


def test_birdeye_collects_ohlcv_and_token_trade_pages(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "ohlcv" in url:
            return httpx.Response(200, json={"data": {"items": [{"unixTime": 100, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 10}]}})
        if "token/txs" in url:
            offset = int(request.url.params.get("offset", "0"))
            rows = [{"txHash": f"sig{offset}", "blockUnixTime": 100 + offset, "side": "buy", "owner": "buyer", "price": 1.0}] if offset == 0 else []
            return httpx.Response(200, json={"data": {"items": rows}})
        raise AssertionError(url)

    config = _source_config(tmp_path)
    client = ResearchHttpClient(config, transport=httpx.MockTransport(handler))
    adapter = BirdeyeAdapter(config, client, api_key="configured")
    try:
        candles = asyncio.run(collect_ohlcv_history(adapter, "mint", start_ts=100, end_ts=200, intervals=["1s"]))
        trades = asyncio.run(collect_token_trades(adapter, "mint", start_ts=100, end_ts=200, max_pages=2))
    finally:
        asyncio.run(client.aclose())
    assert candles.records[0]["row_id"] == "birdeye:mint:1s:100"
    assert trades.records[0]["signature"] == "sig0"


def test_source_snapshots_outcomes_and_strict_replay_use_real_timestamps(tmp_path) -> None:
    config = _source_config(tmp_path)
    ResearchStore(config).init_schema()
    token = "mint"
    write_parquet_table(config, "token_identity", [{"row_id": "identity:solana:mint", "token_id": "solana:mint", "chain": "solana", "canonical_address": token, "creation_ts": 100, "launchpad": "pumpswap", "observed_at": 100, "evidence_quality": "direct"}], token=token, data_mode="source")
    write_parquet_table(config, "market_candles", [
        {"row_id": "c1", "token": token, "candle_start": 100, "candle_end": 101, "close": 1.0, "observed_at": 101, "source": "birdeye", "evidence_quality": "direct", "data_mode": "source"},
        {"row_id": "c2", "token": token, "candle_start": 160, "candle_end": 161, "close": 1.3, "observed_at": 161, "source": "birdeye", "evidence_quality": "direct", "data_mode": "source"},
    ], token=token, data_mode="source")
    write_parquet_table(config, "liquidity_observations", [{"row_id": "l1", "token": token, "observed_at": 100, "liquidity_usd": 50_000, "evidence_quality": "direct", "data_mode": "source"}], token=token, data_mode="source")
    write_parquet_table(config, "transaction_fees", [{"row_id": "f1", "token": token, "observed_at": 100, "block_time": 100, "total_network_fee_sol": 0.000005, "transaction_success": True, "fee_payer": "payer", "evidence_quality": "direct", "data_mode": "source"}], token=token, data_mode="source")

    snapshots = build_historical_snapshots(config, token)
    outcomes = build_historical_outcomes(config, token)
    with ResearchStore(config).connect() as conn:
        rows = conn.execute("SELECT * FROM research_snapshots WHERE token_id=? AND data_mode='source' ORDER BY snapshot_ts", (token,)).fetchall()
    replay = replay_token_snapshots(rows, profile="AGGRESSIVE_CATALYST_RUNNER", intended_size_usd=100, strict_historical_replay=True)
    assert snapshots["snapshots_created"] > 1
    assert all(int(row["snapshot_ts"]) > 0 for row in rows)
    assert outcomes["outcomes_created"] > 1
    assert replay["summary"]["strict_historical_replay"] is True


def test_strict_market_adapter_keeps_missing_unavailable() -> None:
    market = snapshot_market({"price": {"value": None}, "liquidity": {"value": None}, "missing_is_not_zero": True}, intended_size_usd=100, strict_historical_replay=True)
    assert market["price_usd"] is None
    assert market["sell_route_ok"] is False
    assert market["execution_quality"] == "insufficient_data"


def test_source_totals_reject_fixture_rows(tmp_path) -> None:
    config = _source_config(tmp_path)
    store = ResearchStore(config)
    store.init_schema()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO research_snapshots (snapshot_id, token_id, snapshot_ts, snapshot_label, features_json, quality_json, source_hashes_json, data_mode) VALUES ('s', 'fixture-token', 1, 'bad', '{}', '{}', '[]', 'source')"
        )
    try:
        _assert_no_fixture_source_totals(config)
    except RuntimeError as exc:
        assert "fixture_rows_in_source_totals" in str(exc)
    else:
        raise AssertionError("expected fixture source total assertion")

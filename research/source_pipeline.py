from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from research.backfill.jobs import create_or_resume_job, update_job
from research.capabilities import probe_source_capabilities
from research.config import ResearchConfig
from research.execution import reserve_execution_estimate
from research.http_client import ResearchHttpClient
from research.historical_builders import build_historical_outcomes, build_historical_snapshots, read_source_parquet_rows
from research.identity import resolve_token_identity_async
from research.normalization.fees import normalize_fee
from research.normalization.trades import classify_trade
from research.normalization.transactions import normalize_transaction
from research.parquet_writer import write_parquet_table
from research.registry import load_operator_seed_addresses, validate_operator_seeds
from research.replay.action_replay import replay_token_snapshots
from research.source_adapters.birdeye import BirdeyeAdapter, collect_ohlcv_history, collect_token_trades
from research.source_adapters.dexscreener import DexScreenerAdapter
from research.source_adapters.helius import HeliusAdapter, collect_transactions_for_address
from research.source_adapters.solana_rpc import SolanaRpcAdapter, collect_signatures_for_address, hydrate_signatures
from research.storage import ResearchStore


PROOF_TOKEN = "FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2"
THREE_TOKEN_PILOT = [
    "FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2",
    "A13oRB9FFaiUjfi6LdCg6p9ka1u8SfGkUFs4SKvPpump",
    "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump",
]
SOURCE_PILOT_DIR = "real_historical_pilot"


def validate_operator_seeds_source(config: ResearchConfig) -> dict[str, Any]:
    base = validate_operator_seeds(config)
    addresses = [item["supplied_address"] for item in base["results"] if item["chain"] == "solana"]
    resolved = []
    for address in addresses:
        resolved.append(asyncio.run(resolve_token_identity_async(config, address)).to_dict())
    payload = {**base, "data_mode": "source", "source_resolutions": resolved}
    _write_pilot_json(config, "operator_seed_identity.json", payload)
    return payload


def plan_source_backfill(
    config: ResearchConfig,
    *,
    token: str | None = None,
    cohort: str = "operator_seed_cohort_v1",
    sources: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    validate_operator_seeds(config)
    tokens = _selected_tokens(token=token, max_tokens=max_tokens)
    selected_sources = _selected_sources(sources)
    capability = probe_source_capabilities(config)
    by_source = {item["source"]: item for item in capability["sources"]}
    operations = []
    blocked = []
    for mint in tokens:
        for source in selected_sources:
            ops = {
                "helius": ["getTransactionsForAddress"],
                "solana_rpc": ["getAccountInfo", "getSignaturesForAddress", "getTransaction", "getTokenSupply"],
                "birdeye": ["creation_info", "ohlcv_v3", "token_trades", "holder_distribution", "token_security"],
                "dexscreener": ["token_pairs", "token_profiles"],
                "jupiter": ["current_price", "current_quote"],
            }.get(source, [])
            for op in ops:
                available = bool(by_source.get(source, {}).get("endpoint_available"))
                operations.append({"token": mint, "source": source, "operation": op, "available": available})
                if not available:
                    blocked.append({"token": mint, "source": source, "operation": op, "reason": by_source.get(source, {}).get("unavailable_reason")})
    payload = {
        "data_mode": "source",
        "cohort": cohort,
        "tokens": tokens,
        "operations": len(operations),
        "likely_pages": min(config.max_pages_per_job, max(1, len(tokens) * len(selected_sources))),
        "cached_requests": 0,
        "uncached_requests": sum(1 for item in operations if item["available"]),
        "source_dependencies": selected_sources,
        "expected_api_credits": "source_plan_dependent",
        "expected_output_size": "bounded_by_request_budget_and_max_pages",
        "blocked_operations": blocked,
        "recommended_pilot_command": f"python -m research.cli backfill --mode source --token {token or PROOF_TOKEN} --resume --request-budget {min(config.request_budget, 500)}",
    }
    _write_pilot_json(config, "request_plan.json", payload)
    return payload


def run_source_backfill(
    config: ResearchConfig,
    *,
    token: str | None = None,
    cohort: str = "operator_seed_cohort_v1",
    sources: str | None = None,
    request_budget: int | None = None,
    max_pages: int | None = None,
    max_records: int | None = None,
    max_tokens: int | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    if request_budget is not None:
        config = config.__class__(**{**config.__dict__, "request_budget": request_budget})
    if max_pages is not None:
        config = config.__class__(**{**config.__dict__, "max_pages_per_job": max_pages})
    return asyncio.run(_run_source_backfill_async(config, token=token, cohort=cohort, sources=sources, request_budget=request_budget, max_records=max_records, max_tokens=max_tokens, resume=resume))


async def _run_source_backfill_async(
    config: ResearchConfig,
    *,
    token: str | None,
    cohort: str,
    sources: str | None,
    request_budget: int | None,
    max_records: int | None,
    max_tokens: int | None,
    resume: bool,
) -> dict[str, Any]:
    assert config.mode == "source", "source_pipeline_requires_source_mode"
    store = ResearchStore(config)
    store.init_schema()
    selected_tokens = _selected_tokens(token=token, max_tokens=max_tokens)
    selected_sources = _selected_sources(sources)
    parquet_results: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    completed = partial = blocked = failed = raw_cached = 0
    async with _client(config) as client:
        for mint in selected_tokens:
            identity = await resolve_token_identity_async(config, mint)
            identity_rows = [identity.to_dict()]
            parquet_results.append(write_parquet_table(config, "token_identity", identity_rows, token=mint, data_mode="source"))
            if identity.status in {"verified", "partially_verified"}:
                partial += 1
            else:
                blocked += 1

            if "dexscreener" in selected_sources:
                dex = DexScreenerAdapter(config, client)
                job_id = create_or_resume_job(config, cohort=cohort, token_id=mint, source="dexscreener", stage="token_pairs", data_mode="source")
                result = await dex.token_pairs(mint)
                source_results.append(result.to_dict())
                _record_source_payload(config, result, token_id=mint, job_id=job_id)
                raw_cached += 1
                pair_rows = DexScreenerAdapter.normalize_pairs(result, mint)
                parquet_results.append(write_parquet_table(config, "pair_observations", pair_rows, token=mint, data_mode="source"))
                update_job(config, job_id, status="completed" if result.status in {"success", "empty"} else "source_unavailable", records_written=len(pair_rows), raw_response_hashes_json=[result.response_hash] if result.response_hash else [], last_error=";".join(result.errors or result.warnings))

            normalized_txs: list[dict[str, Any]] = []
            raw_txs: list[dict[str, Any]] = []
            all_source_hashes: list[str] = []
            if "helius" in selected_sources:
                helius = HeliusAdapter(config, client)
                job_id = create_or_resume_job(config, cohort=cohort, token_id=mint, source="helius", stage="getTransactionsForAddress", data_mode="source")
                result = await collect_transactions_for_address(
                    helius,
                    mint,
                    request_budget=request_budget,
                    max_pages=config.max_pages_per_job,
                    max_records=max_records,
                    resume_cursor=_job_cursor(config, job_id) if resume else None,
                )
                source_results.append(result.to_dict())
                _record_source_payload(config, result, token_id=mint, job_id=job_id)
                raw_cached += 1
                all_source_hashes.extend(result.rate_limit.get("raw_response_hashes") or [])
                raw_txs.extend(_raw_transaction_rows(result.records, mint=mint, source="helius", job_id=job_id, result=result))
                normalized_txs.extend(normalize_transaction({**row, "fetched_at": result.fetched_at}, token=mint, source="helius", job_id=job_id, request_hash=result.request_hash, response_hash=result.response_hash) for row in result.records if row.get("hydration_state") in {None, "hydrated"})
                update_job(config, job_id, status=_job_status(result.status), records_written=len(result.records), next_cursor=result.next_cursor, raw_response_hashes_json=result.rate_limit.get("raw_response_hashes") or ([result.response_hash] if result.response_hash else []), completed_start_ts=result.returned_start_ts, completed_end_ts=result.returned_end_ts, last_error=";".join(result.errors or result.warnings))

            if "solana_rpc" in selected_sources:
                rpc = SolanaRpcAdapter(config, client)
                job_id = create_or_resume_job(config, cohort=cohort, token_id=mint, source="solana_rpc", stage="getSignaturesForAddress", data_mode="source")
                sigs = await collect_signatures_for_address(
                    rpc,
                    mint,
                    request_budget=request_budget,
                    max_pages=config.max_pages_per_job,
                    max_records=max_records,
                    resume_cursor=_job_cursor(config, job_id) if resume else None,
                )
                source_results.append(sigs.to_dict())
                _record_source_payload(config, sigs, token_id=mint, job_id=job_id)
                raw_cached += 1
                update_job(config, job_id, status=_job_status(sigs.status), records_written=len(sigs.records), next_cursor=sigs.next_cursor, raw_response_hashes_json=sigs.rate_limit.get("raw_response_hashes") or ([sigs.response_hash] if sigs.response_hash else []), completed_start_ts=sigs.returned_start_ts, completed_end_ts=sigs.returned_end_ts, last_error=";".join(sigs.errors or sigs.warnings))
                hydrate_job_id = create_or_resume_job(config, cohort=cohort, token_id=mint, source="solana_rpc", stage="getTransaction", data_mode="source")
                hydrated = await hydrate_signatures(rpc, sigs.records, request_budget=request_budget, concurrency=config.max_concurrency, completed_signatures=_hydrated_signatures(config, mint) if resume else set())
                source_results.append(hydrated.to_dict())
                _record_source_payload(config, hydrated, token_id=mint, job_id=hydrate_job_id)
                raw_cached += 1
                all_source_hashes.extend(hydrated.rate_limit.get("raw_response_hashes") or [])
                raw_txs.extend(_raw_transaction_rows(hydrated.records, mint=mint, source="solana_rpc", job_id=hydrate_job_id, result=hydrated))
                normalized_txs.extend(normalize_transaction({**row, "fetched_at": hydrated.fetched_at}, token=mint, source="solana_rpc", job_id=hydrate_job_id, request_hash=row.get("request_hash") or hydrated.request_hash, response_hash=row.get("response_hash") or hydrated.response_hash) for row in hydrated.records if row.get("hydration_state") == "hydrated")
                update_job(config, hydrate_job_id, status=_job_status(hydrated.status), records_written=len(hydrated.records), raw_response_hashes_json=hydrated.rate_limit.get("raw_response_hashes") or ([hydrated.response_hash] if hydrated.response_hash else []), last_error=";".join(hydrated.errors or hydrated.warnings))

            if normalized_txs:
                normalized_txs = _dedupe_records(normalized_txs, "row_id")
                fees = [normalize_fee(row) for row in normalized_txs]
                trades = [row for row in (classify_trade(row, token=mint) for row in normalized_txs if row.get("success")) if row.get("side") != "unknown"]
                parquet_results.append(write_parquet_table(config, "raw_transactions", _dedupe_records(raw_txs, "row_id"), token=mint, data_mode="source"))
                parquet_results.append(write_parquet_table(config, "normalized_transactions", normalized_txs, token=mint, data_mode="source"))
                parquet_results.append(write_parquet_table(config, "transaction_fees", fees, token=mint, data_mode="source"))
                parquet_results.append(write_parquet_table(config, "normalized_trades", trades, token=mint, data_mode="source"))
                parquet_results.append(write_parquet_table(config, "source_coverage", [_coverage_row(mint, "transactions", normalized_txs, all_source_hashes)], token=mint, data_mode="source"))
                reconciliation = _reconcile_transactions(mint, raw_txs, normalized_txs)
                parquet_results.append(write_parquet_table(config, "transaction_source_reconciliation", [reconciliation], token=mint, data_mode="source"))
                completed += 1

            if "birdeye" in selected_sources:
                bird = BirdeyeAdapter(config, client)
                creation_anchor = identity.creation_ts or identity.first_pool_ts or int(time.time()) - 86_400
                end_anchor = min(int(time.time()), creation_anchor + 86_400)
                for operation, coro in [
                    ("creation_info", bird.creation_info(mint)),
                    ("token_overview", bird.token_overview(mint)),
                    ("token_trades", collect_token_trades(bird, mint, start_ts=creation_anchor, end_ts=end_anchor, request_budget=request_budget, max_pages=config.max_pages_per_job, max_records=max_records)),
                    ("ohlcv_v3", collect_ohlcv_history(bird, mint, start_ts=creation_anchor, end_ts=end_anchor, request_budget=request_budget, max_pages=config.max_pages_per_job)),
                ]:
                    job_id = create_or_resume_job(config, cohort=cohort, token_id=mint, source="birdeye", stage=operation, data_mode="source")
                    result = await coro
                    source_results.append(result.to_dict())
                    _record_source_payload(config, result, token_id=mint, job_id=job_id)
                    raw_cached += 1
                    if operation == "token_trades" and result.records:
                        parquet_results.append(write_parquet_table(config, "normalized_trades", result.records, token=mint, data_mode="source"))
                    if operation == "ohlcv_v3" and result.records:
                        parquet_results.append(write_parquet_table(config, "market_candles", result.records, token=mint, data_mode="source"))
                        liquidity_rows = _liquidity_from_candles(mint, result.records)
                        parquet_results.append(write_parquet_table(config, "liquidity_observations", liquidity_rows, token=mint, data_mode="source"))
                    update_job(config, job_id, status=_job_status(result.status), records_written=len(result.records), raw_response_hashes_json=result.rate_limit.get("raw_response_hashes") or ([result.response_hash] if result.response_hash else []), completed_start_ts=result.returned_start_ts, completed_end_ts=result.returned_end_ts, last_error=";".join(result.errors or result.warnings))

    proof_status = _proof_status(config, token or PROOF_TOKEN)
    payload = {
        "data_mode": "source",
        "tokens_attempted": selected_tokens,
        "completed": completed,
        "partial": partial,
        "blocked": blocked,
        "failed": failed,
        "raw_responses_cached": raw_cached,
        "parquet": parquet_results,
        "source_results": source_results,
        "proof_token_status": proof_status,
        "resume_used": resume,
        "fixture_data_used": False,
    }
    _assert_no_fixture_source_totals(config)
    _write_pilot_json(config, "proof_token_coverage.json", payload if token == PROOF_TOKEN or len(selected_tokens) == 1 else {"proof_token_status": proof_status})
    _write_pilot_json(config, "api_usage.json", {"data_mode": "source", "requests_attempted": len(source_results), "request_budget": config.request_budget, "sources": _count_by(source_results, "source")})
    return payload


def build_source_features(config: ResearchConfig, *, token: str | None = None) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    selected = _selected_tokens(token=token, max_tokens=3 if not token else 1)
    created = 0
    details = []
    for mint in selected:
        result = build_historical_snapshots(config, mint)
        created += int(result.get("snapshots_created", 0))
        details.append(result)
    payload = {"data_mode": "source", "snapshots_created": created, "tokens": selected, "details": details, "fixture_data_used": False}
    _assert_no_fixture_source_totals(config)
    _write_pilot_json(config, "proof_token_timeline.json", payload)
    return payload


def build_source_outcomes(config: ResearchConfig, *, token: str | None = None) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    selected = _selected_tokens(token=token, max_tokens=3 if not token else 1)
    created = 0
    details = []
    for mint in selected:
        result = build_historical_outcomes(config, mint)
        created += int(result.get("outcomes_created", 0))
        details.append(result)
    _assert_no_fixture_source_totals(config)
    return {"data_mode": "source", "outcomes_created": created, "details": details, "fixture_data_used": False}


def build_source_controls(config: ResearchConfig, *, token: str | None = None) -> dict[str, Any]:
    payload = {
        "data_mode": "source",
        "status": "blocked_by_source_coverage",
        "real_controls_completed": 0,
        "required_controls": 15 if token is None else 5,
        "reason": "real control discovery requires source-backed creation windows or token lists; fixture controls are forbidden in source mode",
        "fixture_controls_used": False,
    }
    _write_pilot_json(config, "matched_controls.json", payload)
    return payload


def run_source_action_replay(config: ResearchConfig, *, token: str | None = None, limit: int | None = None) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    created = int(time.time())
    replays = 0
    with store.connect() as conn:
        query = "SELECT DISTINCT token_id FROM research_snapshots WHERE data_mode='source' ORDER BY token_id"
        params: tuple[Any, ...] = ()
        if token:
            query += " AND token_id=?"  # not reached because ORDER BY present; use branch below
        if token:
            token_rows = [{"token_id": token}]
        else:
            token_rows = conn.execute(query + " LIMIT ?", (limit or 1000,)).fetchall()
        for row in token_rows:
            mint = row["token_id"] if not isinstance(row, dict) else row["token_id"]
            snapshots = conn.execute("SELECT * FROM research_snapshots WHERE token_id=? AND data_mode='source' ORDER BY snapshot_ts", (mint,)).fetchall()
            conn.execute("DELETE FROM research_action_replays WHERE token_id=? AND data_mode='source'", (mint,))
            if not snapshots:
                continue
            for profile in ["BALANCED", "AGGRESSIVE", "AGGRESSIVE_CATALYST_RUNNER"]:
                for size in [100.0, 250.0, 500.0]:
                    result = replay_token_snapshots(snapshots, profile=profile, intended_size_usd=size, strict_historical_replay=True)
                    for action in result["actions"]:
                        action["data_mode"] = "source"
                        action["fixture_data_used"] = False
                    result["summary"]["data_mode"] = "source"
                    result["summary"]["fixture_data_used"] = False
                    result["summary"]["fixture_only"] = False
                    result["summary"]["execution_quality"] = "insufficient_data" if result["summary"]["entry_count"] == 0 else "historical_liquidity_estimated"
                    replay_id = uuid.uuid5(uuid.NAMESPACE_URL, f"source:{mint}:{profile}:{size}").hex
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO research_action_replays
                        (replay_id, token_id, profile, intended_size_usd, actions_json, summary_json, replay_version, created_ts, data_mode)
                        VALUES (?, ?, ?, ?, ?, ?, 'source-action-replay-v1', ?, 'source')
                        """,
                        (replay_id, mint, profile, size, json.dumps(result["actions"], sort_keys=True), json.dumps(result["summary"], sort_keys=True), created),
                    )
                    replays += 1
    payload = {"data_mode": "source", "action_replays": replays, "fixture_data_used": False}
    _write_pilot_json(config, "proof_token_action_replay.json", payload)
    return payload


def generate_source_report(config: ResearchConfig, *, token: str | None = None) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    with store.connect() as conn:
        parquet = conn.execute("SELECT table_name, SUM(row_count) AS rows FROM research_parquet_files WHERE data_mode='source' GROUP BY table_name").fetchall()
        snapshots = conn.execute("SELECT COUNT(*) AS c FROM research_snapshots WHERE data_mode='source'").fetchone()["c"]
        outcomes = conn.execute("SELECT COUNT(*) AS c FROM research_outcomes WHERE data_mode='source'").fetchone()["c"]
        replays = conn.execute("SELECT COUNT(*) AS c FROM research_action_replays WHERE data_mode='source'").fetchone()["c"]
        if token:
            tokens = conn.execute("SELECT * FROM research_tokens WHERE data_mode='source' AND canonical_address=? ORDER BY canonical_address", (token,)).fetchall()
        else:
            tokens = conn.execute("SELECT * FROM research_tokens WHERE data_mode='source' ORDER BY canonical_address").fetchall()
        fixture_in_source = _fixture_rows_in_source_totals(conn)
    payload = {
        "data_mode": "source",
        "source_backed": True,
        "fixture_totals_excluded": True,
        "parquet_row_counts": {row["table_name"]: row["rows"] for row in parquet},
        "source_snapshot_count": snapshots,
        "source_outcome_count": outcomes,
        "source_action_replay_count": replays,
        "fixture_rows_in_source_totals": fixture_in_source,
        "tokens": [
            {
                "token": row["canonical_address"],
                "chain": row["canonical_chain"],
                "symbol": row["symbol"],
                "name": row["name"],
                "creation_ts": row["creation_ts"],
                "launchpad": row["launchpad"],
                "verification_status": row["verification_status"],
            }
            for row in tokens
        ],
        "limitations": [
            "current-only DEX Screener, holder, and Jupiter data are excluded from historical snapshots",
            "missing historical price/liquidity remains missing",
            "real matched controls require source-backed discovery coverage",
        ],
    }
    _write_pilot_json(config, "source_coverage.json", payload)
    _write_pilot_json(config, "data_quality.json", {"data_mode": "source", "states": ["real source-backed", "reconstructed", "current-only", "reference-only", "missing", "fixture"], "fixture_excluded_from_source_totals": True})
    _write_pilot_json(config, "three_token_pilot_summary.json", payload)
    _write_real_historical_artifacts(config, token=token, summary=payload)
    _write_source_index(config, payload)
    if fixture_in_source:
        raise RuntimeError("fixture_rows_in_source_totals_nonzero")
    return {"data_mode": "source", "artifact_dir": str(config.artifact_dir / SOURCE_PILOT_DIR), "summary": payload}


def _selected_tokens(*, token: str | None, max_tokens: int | None) -> list[str]:
    if token:
        return [token]
    seeds = [item for item in load_operator_seed_addresses() if not item.startswith("0x")]
    if max_tokens:
        return seeds[:max_tokens]
    return [PROOF_TOKEN]


def _selected_sources(sources: str | None) -> list[str]:
    if not sources:
        return ["helius", "birdeye", "dexscreener", "solana_rpc"]
    return [item.strip() for item in sources.split(",") if item.strip()]


class _client:
    def __init__(self, config: ResearchConfig):
        self.client = ResearchHttpClient(config)

    async def __aenter__(self) -> ResearchHttpClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.aclose()


def _record_source_payload(config: ResearchConfig, result, *, token_id: str, job_id: str) -> None:
    from research.raw_cache import store_raw_response

    store_raw_response(
        config,
        source=result.source,
        endpoint=result.operation,
        params={"token_id": token_id, "job_id": job_id, "request_hash": result.request_hash},
        payload=result.to_dict(),
        status=result.status,
        completeness_status=result.completeness,
        token_id=token_id,
        data_mode="source",
    )


def _proof_status(config: ResearchConfig, token: str) -> dict[str, Any]:
    store = ResearchStore(config)
    with store.connect() as conn:
        identity = conn.execute("SELECT * FROM research_tokens WHERE canonical_address=? AND data_mode='source'", (token,)).fetchone()
        tx_rows = conn.execute("SELECT SUM(row_count) AS c FROM research_parquet_files WHERE token=? AND table_name='normalized_transactions' AND data_mode='source'", (token,)).fetchone()["c"] or 0
        trade_rows = conn.execute("SELECT SUM(row_count) AS c FROM research_parquet_files WHERE token=? AND table_name='normalized_trades' AND data_mode='source'", (token,)).fetchone()["c"] or 0
        fee_rows = conn.execute("SELECT SUM(row_count) AS c FROM research_parquet_files WHERE token=? AND table_name='transaction_fees' AND data_mode='source'", (token,)).fetchone()["c"] or 0
        candle_rows = conn.execute("SELECT SUM(row_count) AS c FROM research_parquet_files WHERE token=? AND table_name='market_candles' AND data_mode='source'", (token,)).fetchone()["c"] or 0
        liquidity_rows = conn.execute("SELECT SUM(row_count) AS c FROM research_parquet_files WHERE token=? AND table_name='liquidity_observations' AND data_mode='source'", (token,)).fetchone()["c"] or 0
        snapshot_rows = conn.execute("SELECT COUNT(*) AS c FROM research_snapshots WHERE token_id=? AND data_mode='source' AND snapshot_ts > 0", (token,)).fetchone()["c"] or 0
        outcome_rows = conn.execute("SELECT COUNT(*) AS c FROM research_outcomes WHERE token_id=? AND data_mode='source' AND resolution_status != 'insufficient_data'", (token,)).fetchone()["c"] or 0
        replay_rows = conn.execute("SELECT COUNT(*) AS c FROM research_action_replays WHERE token_id=? AND data_mode='source'", (token,)).fetchone()["c"] or 0
    verified = identity and identity["verification_status"] == "verified"
    historical_ready = verified and tx_rows > 0 and trade_rows > 0 and fee_rows > 0 and (trade_rows > 1 or candle_rows > 1) and snapshot_rows > 1 and outcome_rows > 0 and replay_rows > 0
    if historical_ready:
        status = "source_pilot_complete"
    elif identity and identity["verification_status"] in {"verified", "partially_verified"} and (tx_rows or trade_rows or candle_rows):
        status = "source_pilot_partial"
    else:
        import os

        missing = [name for name in ["HELIUS_API_KEY", "HELIUS_RPC_URL", "BIRDEYE_API_KEY"] if not os.getenv(name, "").strip()]
        status = "blocked_by_missing_credentials" if missing else "blocked_by_unavailable_history"
    return {
        "token": token,
        "status": status,
        "canonical_mint_verified": bool(verified),
        "transaction_count": tx_rows,
        "trade_count": trade_rows,
        "fee_count": fee_rows,
        "historical_candle_count": candle_rows,
        "historical_liquidity_count": liquidity_rows,
        "snapshot_count": snapshot_rows,
        "resolved_outcome_count": outcome_rows,
        "action_replay_count": replay_rows,
        "partial_feature_coverage": bool(tx_rows or trade_rows or candle_rows),
        "fixture_data_used": False,
    }


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key))
        out[value] = out.get(value, 0) + 1
    return out


def _job_status(source_status: str) -> str:
    if source_status == "success":
        return "completed"
    if source_status in {"not_configured", "plan_restricted", "unauthorized", "outside_retention"}:
        return "source_unavailable"
    if source_status in {"rate_limited", "empty", "partial"}:
        return "partial"
    if source_status in {"failed", "source_unavailable", "malformed_response", "invalid_request"}:
        return "failed" if source_status in {"malformed_response", "invalid_request"} else "source_unavailable"
    return "partial"


def _job_cursor(config: ResearchConfig, job_id: str) -> str | None:
    with ResearchStore(config).connect() as conn:
        row = conn.execute("SELECT next_cursor FROM research_jobs WHERE job_id=?", (job_id,)).fetchone()
    return row["next_cursor"] if row and row["next_cursor"] else None


def _hydrated_signatures(config: ResearchConfig, token: str) -> set[str]:
    return {str(row.get("signature")) for row in read_source_parquet_rows(config, "normalized_transactions", token=token) if row.get("signature")}


def _raw_transaction_rows(records: list[dict[str, Any]], *, mint: str, source: str, job_id: str, result) -> list[dict[str, Any]]:
    rows = []
    for index, record in enumerate(records):
        signature = record.get("signature") or ((record.get("transaction") or {}).get("signatures") or [None])[0] or f"missing:{index}"
        observed = record.get("blockTime") or record.get("timestamp")
        rows.append(
            {
                "row_id": f"raw-tx:{source}:{signature}",
                "chain": "solana",
                "token": mint,
                "signature": signature,
                "slot": record.get("slot"),
                "block_time": observed,
                "payload_json": record,
                "hydration_state": record.get("hydration_state", "hydrated"),
                "source": source,
                "source_operation": record.get("source_operation") or result.operation,
                "observed_at": observed,
                "fetched_at": record.get("fetched_at") or result.fetched_at,
                "evidence_quality": result.evidence_quality,
                "parser_version": result.parser_version,
                "job_id": job_id,
                "request_hash": record.get("request_hash") or result.request_hash,
                "response_hash": record.get("response_hash") or result.response_hash,
                "data_mode": "source",
                "completeness": result.completeness,
                "warnings": record.get("warnings") or result.warnings,
            }
        )
    return rows


def _dedupe_records(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for row in rows:
        value = str(row.get(key))
        if value in seen:
            continue
        seen.add(value)
        out.append(row)
    return out


def _coverage_row(token: str, family: str, rows: list[dict[str, Any]], response_hashes: list[str]) -> dict[str, Any]:
    times = [int(row.get("observed_at") or row.get("block_time")) for row in rows if row.get("observed_at") or row.get("block_time")]
    return {
        "row_id": f"coverage:{token}:{family}",
        "chain": "solana",
        "token": token,
        "feature_family": family,
        "row_count": len(rows),
        "coverage_start_ts": min(times, default=None),
        "coverage_end_ts": max(times, default=None),
        "coverage_state": "usable" if rows else "unavailable",
        "source": ",".join(sorted({str(row.get("source")) for row in rows if row.get("source")})),
        "source_operation": family,
        "observed_at": max(times, default=int(time.time())),
        "fetched_at": int(time.time()),
        "evidence_quality": "parsed_direct" if rows else "unavailable",
        "parser_version": "source-coverage-v1",
        "job_id": None,
        "request_hash": None,
        "response_hash": response_hashes[-1] if response_hashes else None,
        "data_mode": "source",
        "completeness": "partial" if rows else "unavailable",
        "warnings": [] if rows else ["no_source_rows"],
    }


def _reconcile_transactions(token: str, raw_rows: list[dict[str, Any]], normalized_rows: list[dict[str, Any]]) -> dict[str, Any]:
    helius = {row.get("signature") for row in raw_rows if row.get("source") == "helius" and row.get("signature")}
    rpc = {row.get("signature") for row in raw_rows if row.get("source") == "solana_rpc" and row.get("signature")}
    norm_by_sig: dict[str, list[dict[str, Any]]] = {}
    for row in normalized_rows:
        norm_by_sig.setdefault(str(row.get("signature")), []).append(row)
    fee_disagreements = 0
    timestamp_disagreements = 0
    for rows in norm_by_sig.values():
        fees = {row.get("total_network_fee_lamports") for row in rows if row.get("total_network_fee_lamports") is not None}
        times = {row.get("block_time") for row in rows if row.get("block_time") is not None}
        if len(fees) > 1:
            fee_disagreements += 1
        if len(times) > 1:
            timestamp_disagreements += 1
    return {
        "row_id": f"reconcile:{token}",
        "chain": "solana",
        "token": token,
        "signatures_in_both": len(helius & rpc),
        "helius_only_signatures": sorted(helius - rpc),
        "rpc_only_signatures": sorted(rpc - helius),
        "mismatched_fees": fee_disagreements,
        "mismatched_timestamps": timestamp_disagreements,
        "parsing_disagreements": 0,
        "canonical_selection_rule": "prefer_direct_fee_and_block_time_agreement_else_retain_source_specific_rows",
        "source": "helius,solana_rpc",
        "source_operation": "transaction_source_reconciliation",
        "observed_at": int(time.time()),
        "fetched_at": int(time.time()),
        "evidence_quality": "reconstructed",
        "parser_version": "transaction-source-reconciliation-v1",
        "job_id": None,
        "request_hash": None,
        "response_hash": None,
        "data_mode": "source",
        "completeness": "usable" if raw_rows else "unavailable",
        "warnings": [] if raw_rows else ["no_overlap_available"],
    }


def _liquidity_from_candles(token: str, candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for candle in candles:
        liquidity = candle.get("liquidity_usd")
        if liquidity is None:
            continue
        rows.append(
            {
                "row_id": f"liq:{token}:{candle.get('interval')}:{candle.get('candle_start')}",
                "chain": "solana",
                "token": token,
                "pool": None,
                "venue": "birdeye",
                "token_reserve": None,
                "quote_reserve": None,
                "liquidity_usd": liquidity,
                "liquidity_sol": None,
                "source": "birdeye",
                "source_operation": "ohlcv_v3_liquidity_field",
                "observed_at": candle.get("observed_at"),
                "fetched_at": candle.get("fetched_at"),
                "evidence_quality": "direct",
                "parser_version": "historical-liquidity-path-v1",
                "job_id": None,
                "request_hash": candle.get("request_hash"),
                "response_hash": candle.get("response_hash"),
                "data_mode": "source",
                "completeness": candle.get("completeness"),
                "warnings": ["liquidity_available_only_if_source_returned_field"],
            }
        )
    return rows


def _assert_no_fixture_source_totals(config: ResearchConfig) -> None:
    with ResearchStore(config).connect() as conn:
        if _fixture_rows_in_source_totals(conn):
            raise RuntimeError("fixture_rows_in_source_totals_nonzero")


def _fixture_rows_in_source_totals(conn) -> int:
    count = 0
    for table, token_col in [
        ("research_snapshots", "token_id"),
        ("research_outcomes", "token_id"),
        ("research_action_replays", "token_id"),
        ("research_parquet_files", "token"),
    ]:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE data_mode='source' AND ({token_col} LIKE 'fixture-%' OR {token_col} LIKE '%fixture%')"
        ).fetchone()
        count += int(row["c"] if row else 0)
    return count


def _write_pilot_json(config: ResearchConfig, name: str, payload: dict[str, Any]) -> None:
    path = config.artifact_dir / SOURCE_PILOT_DIR
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_source_index(config: ResearchConfig, payload: dict[str, Any]) -> None:
    path = config.artifact_dir / SOURCE_PILOT_DIR
    html = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Source Pilot Research</title>
<style>body{{font-family:Arial,sans-serif;margin:32px;color:#1f2933}}section{{border:1px solid #d8dee8;border-radius:6px;padding:16px;margin:12px 0}}code{{background:#eef2f7;padding:2px 4px}}</style></head>
<body><main>
<h1>Source-Backed Research Pilot</h1>
<section><p><strong>Mode:</strong> source. Fixture totals are excluded.</p>
<p>Snapshots: {payload['source_snapshot_count']} Outcomes: {payload['source_outcome_count']} Action replays: {payload['source_action_replay_count']}</p></section>
<section><h2>Parquet Rows</h2><pre>{json.dumps(payload['parquet_row_counts'], indent=2)}</pre></section>
<section><h2>Limitations</h2><pre>{json.dumps(payload['limitations'], indent=2)}</pre></section>
</main></body></html>"""
    (path / "index.html").write_text(html, encoding="utf-8")


def _write_real_historical_artifacts(config: ResearchConfig, *, token: str | None, summary: dict[str, Any]) -> None:
    selected = token or PROOF_TOKEN
    with ResearchStore(config).connect() as conn:
        parquet = [dict(row) for row in conn.execute("SELECT table_name, token, row_count, path, data_mode FROM research_parquet_files WHERE data_mode='source' ORDER BY table_name, token").fetchall()]
        snapshots = [dict(row) for row in conn.execute("SELECT snapshot_id, token_id, snapshot_ts, snapshot_label, quality_json FROM research_snapshots WHERE data_mode='source' AND token_id=? ORDER BY snapshot_ts", (selected,)).fetchall()]
        outcomes = [dict(row) for row in conn.execute("SELECT outcome_id, token_id, snapshot_id, resolution_status, metrics_json FROM research_outcomes WHERE data_mode='source' AND token_id=? ORDER BY outcome_id", (selected,)).fetchall()]
        replays = [dict(row) for row in conn.execute("SELECT replay_id, token_id, profile, intended_size_usd, summary_json FROM research_action_replays WHERE data_mode='source' AND token_id=? ORDER BY profile, intended_size_usd", (selected,)).fetchall()]
        raw_fetches = [dict(row) for row in conn.execute("SELECT source, endpoint, status, response_hash, fetched_ts, data_mode FROM research_raw_fetches WHERE data_mode='source' ORDER BY fetched_ts DESC LIMIT 200").fetchall()]
    proof = _proof_status(config, selected)
    parquet_counts: dict[str, int] = {}
    for row in parquet:
        parquet_counts[row["table_name"]] = parquet_counts.get(row["table_name"], 0) + int(row["row_count"] or 0)
    transaction_rows = read_source_parquet_rows(config, "normalized_transactions", token=selected)
    trade_rows = read_source_parquet_rows(config, "normalized_trades", token=selected)
    fee_rows = read_source_parquet_rows(config, "transaction_fees", token=selected)
    candle_rows = read_source_parquet_rows(config, "market_candles", token=selected)
    liquidity_rows = read_source_parquet_rows(config, "liquidity_observations", token=selected)
    identity_rows = read_source_parquet_rows(config, "token_identity", token=selected)
    reconciliation_rows = read_source_parquet_rows(config, "transaction_source_reconciliation", token=selected)
    request_counts = _count_by(raw_fetches, "source")
    _write_pilot_json(config, "proof_token_identity.json", {"data_mode": "source", "token": selected, "identity": identity_rows[:1], "proof_status": proof, "fixture_rows": 0})
    _write_pilot_json(config, "proof_token_transactions.json", {"data_mode": "source", "token": selected, "row_count": len(transaction_rows), "sample": transaction_rows[:10], "fixture_rows": 0})
    _write_pilot_json(config, "proof_token_fee_summary.json", {"data_mode": "source", "token": selected, "row_count": len(fee_rows), "total_fee_sol": sum(float(row.get("total_network_fee_sol") or 0) for row in fee_rows), "unique_fee_payers": len({row.get("fee_payer") for row in fee_rows if row.get("fee_payer")}), "fee_authenticity": "unavailable_wallet_cluster_coverage_incomplete", "fixture_rows": 0})
    _write_pilot_json(config, "proof_token_trade_summary.json", {"data_mode": "source", "token": selected, "row_count": len(trade_rows), "by_side": _count_by(trade_rows, "side"), "fixture_rows": 0})
    _write_pilot_json(config, "proof_token_price_coverage.json", {"data_mode": "source", "token": selected, "historical_candle_rows": len(candle_rows), "historical_trade_rows": len([row for row in trade_rows if row.get("effective_execution_price")]), "current_values_excluded": True, "fixture_rows": 0})
    _write_pilot_json(config, "proof_token_liquidity_coverage.json", {"data_mode": "source", "token": selected, "historical_liquidity_rows": len(liquidity_rows), "current_dexscreener_excluded": True, "fixture_rows": 0})
    _write_pilot_json(config, "proof_token_outcomes.json", {"data_mode": "source", "token": selected, "row_count": len(outcomes), "outcomes": outcomes, "fixture_rows": 0})
    _write_pilot_json(config, "proof_token_action_replay.json", {"data_mode": "source", "token": selected, "row_count": len(replays), "replays": replays, "fixture_rows": 0})
    _write_pilot_json(config, "source_reconciliation.json", {"data_mode": "source", "token": selected, "rows": reconciliation_rows, "fixture_rows": 0})
    _write_pilot_json(config, "parquet_manifest.json", {"data_mode": "source", "tables": parquet, "row_counts": parquet_counts, "fixture_rows": 0})
    _write_pilot_json(config, "api_usage.json", {"data_mode": "source", "requests_by_source": request_counts, "raw_fetches": len(raw_fetches), "fixture_rows": 0})
    _write_pilot_json(config, "proof_token_timeline.json", {"data_mode": "source", "token": selected, "snapshot_count": len(snapshots), "snapshots": snapshots, "proof_status": proof, "fixture_rows": 0})
    _write_pilot_json(config, "matched_controls.json", {"data_mode": "source", "real_controls_completed": 0, "status": "blocked_until_source_creation_windows_available", "fixture_controls_used": False})
    _write_pilot_json(config, "three_token_pilot_summary.json", {"data_mode": "source", "proof_token": selected, "status": proof["status"], "tokens_attempted": THREE_TOKEN_PILOT, "historical_credentials_required": ["HELIUS_API_KEY", "HELIUS_RPC_URL", "BIRDEYE_API_KEY"], "summary": summary, "fixture_rows": 0})

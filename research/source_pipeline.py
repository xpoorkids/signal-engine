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
from research.identity import resolve_token_identity_async
from research.normalization.fees import normalize_fee
from research.normalization.trades import classify_trade
from research.normalization.transactions import normalize_transaction
from research.outcomes.labels import OUTCOME_VERSION, excursion_metrics, target_before_stop
from research.parquet_writer import write_parquet_table
from research.registry import load_operator_seed_addresses, validate_operator_seeds
from research.replay.action_replay import replay_token_snapshots
from research.source_adapters.birdeye import BirdeyeAdapter
from research.source_adapters.dexscreener import DexScreenerAdapter
from research.source_adapters.helius import HeliusAdapter
from research.source_adapters.solana_rpc import SolanaRpcAdapter
from research.storage import ResearchStore


PROOF_TOKEN = "FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2"
THREE_TOKEN_PILOT = [
    "FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2",
    "A13oRB9FFaiUjfi6LdCg6p9ka1u8SfGkUFs4SKvPpump",
    "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump",
]
SOURCE_PILOT_DIR = "source_pilot"


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
    return asyncio.run(_run_source_backfill_async(config, token=token, cohort=cohort, sources=sources, max_records=max_records, max_tokens=max_tokens, resume=resume))


async def _run_source_backfill_async(
    config: ResearchConfig,
    *,
    token: str | None,
    cohort: str,
    sources: str | None,
    max_records: int | None,
    max_tokens: int | None,
    resume: bool,
) -> dict[str, Any]:
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
            if "helius" in selected_sources:
                helius = HeliusAdapter(config, client)
                job_id = create_or_resume_job(config, cohort=cohort, token_id=mint, source="helius", stage="getTransactionsForAddress", data_mode="source")
                result = await helius.get_transactions_for_address(mint, limit=min(max_records or 100, 100))
                source_results.append(result.to_dict())
                _record_source_payload(config, result, token_id=mint, job_id=job_id)
                raw_cached += 1
                normalized_txs.extend(normalize_transaction({**row, "fetched_at": result.fetched_at}, token=mint, source="helius", job_id=job_id, request_hash=result.request_hash, response_hash=result.response_hash) for row in result.records)
                update_job(config, job_id, status=_job_status(result.status), records_written=len(result.records), next_cursor=result.next_cursor, raw_response_hashes_json=[result.response_hash] if result.response_hash else [], last_error=";".join(result.errors or result.warnings))

            if "solana_rpc" in selected_sources:
                rpc = SolanaRpcAdapter(config, client)
                job_id = create_or_resume_job(config, cohort=cohort, token_id=mint, source="solana_rpc", stage="getSignaturesForAddress", data_mode="source")
                sigs = await rpc.get_signatures_for_address(mint, limit=min(max_records or 25, 1000))
                source_results.append(sigs.to_dict())
                _record_source_payload(config, sigs, token_id=mint, job_id=job_id)
                raw_cached += 1
                update_job(config, job_id, status=_job_status(sigs.status), records_written=len(sigs.records), next_cursor=(sigs.records[-1].get("signature") if sigs.records and isinstance(sigs.records[-1], dict) else None), raw_response_hashes_json=[sigs.response_hash] if sigs.response_hash else [], last_error=";".join(sigs.errors or sigs.warnings))

            if normalized_txs:
                fees = [normalize_fee(row) for row in normalized_txs]
                trades = [classify_trade(row, token=mint) for row in normalized_txs if row.get("success")]
                parquet_results.append(write_parquet_table(config, "normalized_transactions", normalized_txs, token=mint, data_mode="source"))
                parquet_results.append(write_parquet_table(config, "transaction_fees", fees, token=mint, data_mode="source"))
                parquet_results.append(write_parquet_table(config, "normalized_trades", trades, token=mint, data_mode="source"))
                completed += 1

            if "birdeye" in selected_sources:
                bird = BirdeyeAdapter(config, client)
                for operation, coro in [
                    ("creation_info", bird.creation_info(mint)),
                    ("token_overview", bird.token_overview(mint)),
                    ("token_trades", bird.token_trades(mint, limit=min(max_records or 50, 50))),
                ]:
                    job_id = create_or_resume_job(config, cohort=cohort, token_id=mint, source="birdeye", stage=operation, data_mode="source")
                    result = await coro
                    source_results.append(result.to_dict())
                    _record_source_payload(config, result, token_id=mint, job_id=job_id)
                    raw_cached += 1
                    update_job(config, job_id, status=_job_status(result.status), records_written=len(result.records), raw_response_hashes_json=[result.response_hash] if result.response_hash else [], last_error=";".join(result.errors or result.warnings))

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
    _write_pilot_json(config, "proof_token_coverage.json", payload if token == PROOF_TOKEN or len(selected_tokens) == 1 else {"proof_token_status": proof_status})
    _write_pilot_json(config, "api_usage.json", {"data_mode": "source", "requests_attempted": len(source_results), "request_budget": config.request_budget, "sources": _count_by(source_results, "source")})
    return payload


def build_source_features(config: ResearchConfig, *, token: str | None = None) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    selected = _selected_tokens(token=token, max_tokens=3 if not token else 1)
    created = 0
    with store.connect() as conn:
        for mint in selected:
            rows = conn.execute("SELECT * FROM research_parquet_files WHERE token=? AND data_mode='source'", (mint,)).fetchall()
            if not rows:
                continue
            observed_ts = 0
            quality = {"overall": "partial", "data_mode": "source", "current_only_excluded_from_history": True}
            features = {
                "token": mint,
                "price": {"value": None, "state": "missing", "source": None},
                "liquidity": {"value": None, "state": "missing", "source": None},
                "transaction_rows": {"value": sum(row["row_count"] for row in rows if row["table_name"] == "normalized_transactions"), "state": "computed"},
                "fee_rows": {"value": sum(row["row_count"] for row in rows if row["table_name"] == "transaction_fees"), "state": "computed"},
                "trade_rows": {"value": sum(row["row_count"] for row in rows if row["table_name"] == "normalized_trades"), "state": "computed"},
                "missing_is_not_zero": True,
                "data_mode": "source",
            }
            sid = uuid.uuid5(uuid.NAMESPACE_URL, f"source-snapshot:{mint}:earliest_available").hex
            conn.execute(
                "DELETE FROM research_snapshots WHERE token_id=? AND data_mode='source' AND snapshot_label='earliest_available_source'",
                (mint,),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO research_snapshots
                (snapshot_id, token_id, snapshot_ts, snapshot_label, features_json, quality_json, source_hashes_json, data_mode)
                VALUES (?, ?, ?, 'earliest_available_source', ?, ?, '[]', 'source')
                """,
                (sid, mint, observed_ts, json.dumps(features, sort_keys=True), json.dumps(quality, sort_keys=True)),
            )
            created += 1
    payload = {"data_mode": "source", "snapshots_created": created, "tokens": selected, "fixture_data_used": False}
    _write_pilot_json(config, "proof_token_timeline.json", payload)
    return payload


def build_source_outcomes(config: ResearchConfig, *, token: str | None = None) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    selected = _selected_tokens(token=token, max_tokens=3 if not token else 1)
    created = 0
    with store.connect() as conn:
        for mint in selected:
            rows = conn.execute("SELECT * FROM research_snapshots WHERE token_id=? AND data_mode='source' ORDER BY snapshot_ts", (mint,)).fetchall()
            if not rows:
                continue
            labels = {"runner_3x": None, "major_runner_10x": None, "execution_failed_winner": None, "source_backed": True, "fixture_only": False}
            metrics = {"resolution_status": "insufficient_data", "outcome_quality": "insufficient_data", "reference_price_return": None, "executable_estimated_return": None}
            outcome_id = uuid.uuid5(uuid.NAMESPACE_URL, f"source-outcome:{mint}:{OUTCOME_VERSION}").hex
            conn.execute(
                """
                INSERT OR REPLACE INTO research_outcomes
                (outcome_id, token_id, labels_json, metrics_json, resolution_status, outcome_version, data_mode)
                VALUES (?, ?, ?, ?, 'insufficient_data', ?, 'source')
                """,
                (outcome_id, mint, json.dumps(labels, sort_keys=True), json.dumps(metrics, sort_keys=True), OUTCOME_VERSION),
            )
            created += 1
    return {"data_mode": "source", "outcomes_created": created, "resolution_status": "insufficient_data_without_historical_price_path", "fixture_data_used": False}


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
            if not snapshots:
                continue
            for profile in ["BALANCED", "AGGRESSIVE", "AGGRESSIVE_CATALYST_RUNNER"]:
                for size in [100.0, 250.0, 500.0]:
                    result = replay_token_snapshots(snapshots, profile=profile, intended_size_usd=size)
                    for action in result["actions"]:
                        action["data_mode"] = "source"
                        action["fixture_data_used"] = False
                    result["summary"]["data_mode"] = "source"
                    result["summary"]["fixture_data_used"] = False
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
        tokens = conn.execute("SELECT * FROM research_tokens WHERE data_mode='source' ORDER BY canonical_address").fetchall()
    payload = {
        "data_mode": "source",
        "source_backed": True,
        "fixture_totals_excluded": True,
        "parquet_row_counts": {row["table_name"]: row["rows"] for row in parquet},
        "source_snapshot_count": snapshots,
        "source_outcome_count": outcomes,
        "source_action_replay_count": replays,
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
    _write_source_index(config, payload)
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
    verified = identity and identity["verification_status"] in {"verified", "partially_verified"}
    status = "source_pilot_complete" if verified and tx_rows > 0 and trade_rows > 0 else "blocked_by_missing_credentials_or_source_coverage"
    return {
        "token": token,
        "status": status,
        "canonical_mint_verified": bool(verified),
        "transaction_count": tx_rows,
        "trade_count": trade_rows,
        "partial_feature_coverage": bool(verified and tx_rows > 0),
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

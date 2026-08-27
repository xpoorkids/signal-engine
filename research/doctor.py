from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from research.capabilities import probe_source_capabilities
from research.config import ResearchConfig
from research.storage import ResearchStore


def _can_write_dir(path: Path) -> bool:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".write_probe_{int(time.time())}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _dependency_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run_doctor(config: ResearchConfig) -> dict[str, Any]:
    store = ResearchStore(config)
    blocked: list[str] = []
    degraded: list[str] = []
    try:
        store.init_schema()
        db_writable = True
    except sqlite3.Error as exc:
        db_writable = False
        blocked.append(f"research_db_unavailable:{type(exc).__name__}")

    data_writable = _can_write_dir(config.data_dir)
    artifact_writable = _can_write_dir(config.artifact_dir)
    if not data_writable:
        blocked.append("research_data_dir_not_writable")
    if not artifact_writable:
        blocked.append("artifact_dir_not_writable")

    deps = {name: _dependency_available(name) for name in ("httpx", "duckdb", "polars", "pandas", "pyarrow")}
    for name, ok in deps.items():
        if not ok:
            degraded.append(f"missing_optional_research_dependency:{name}")

    parquet_ok = False
    try:
        if deps.get("pyarrow"):
            import pyarrow as pa
            import pyarrow.parquet as pq

            table = pa.table({"probe": [1]})
            probe_path = config.artifact_dir / "doctor_parquet_probe.parquet"
            pq.write_table(table, probe_path)
            probe_path.unlink(missing_ok=True)
            parquet_ok = True
    except Exception as exc:
        degraded.append(f"parquet_write_failed:{type(exc).__name__}")

    duckdb_ok = False
    try:
        if deps.get("duckdb"):
            import duckdb

            duckdb.connect(":memory:").execute("select 1").fetchall()
            duckdb_ok = True
    except Exception as exc:
        degraded.append(f"duckdb_read_failed:{type(exc).__name__}")

    capability = probe_source_capabilities(config)
    by_source = {item["source"]: item for item in capability["sources"]}
    source_blockers = []
    for source, env_name in {
        "helius": "HELIUS_API_KEY",
        "solana_rpc": "HELIUS_RPC_URL",
        "birdeye": "BIRDEYE_API_KEY",
    }.items():
        if not by_source.get(source, {}).get("api_key_configured"):
            source_blockers.append(f"missing_env:{env_name}")
    if config.mode == "source" and not by_source.get("dexscreener", {}).get("endpoint_available") and source_blockers:
        blocked.extend(source_blockers)
    elif config.mode == "source":
        degraded.extend(source_blockers)
    if config.mode == "source" and not (
        by_source.get("helius", {}).get("endpoint_available")
        or by_source.get("solana_rpc", {}).get("endpoint_available")
        or by_source.get("birdeye", {}).get("endpoint_available")
    ):
        blocked.append("no_historical_source_available")

    jobs: dict[str, int] = {}
    if db_writable:
        with store.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS c FROM research_jobs GROUP BY status").fetchall()
            jobs = {row["status"]: row["c"] for row in rows}
            if jobs.get("failed"):
                degraded.append("failed_jobs_present")
            if jobs.get("partial") or jobs.get("running"):
                degraded.append("incomplete_jobs_present")

    disk = shutil.disk_usage(config.artifact_dir)
    ready = not blocked and config.mode == "source" and (
        by_source.get("helius", {}).get("endpoint_available")
        or by_source.get("solana_rpc", {}).get("endpoint_available")
        or by_source.get("birdeye", {}).get("endpoint_available")
    )
    if config.mode != "source":
        degraded.append("not_in_source_mode")

    return {
        "data_mode": config.mode,
        "db_path": str(config.db_path),
        "data_dir": str(config.data_dir),
        "artifact_dir": str(config.artifact_dir),
        "write_permissions": {
            "database_initialized": db_writable,
            "data_dir": data_writable,
            "artifact_dir": artifact_writable,
        },
        "disk_free_bytes": disk.free,
        "dependencies": deps,
        "parquet_write_capable": parquet_ok,
        "duckdb_read_capable": duckdb_ok,
        "credentials": {
            "helius_api_key_configured": bool(os.getenv("HELIUS_API_KEY", "").strip()),
            "helius_rpc_url_configured": bool(os.getenv("HELIUS_RPC_URL", "").strip()),
            "birdeye_api_key_configured": bool(os.getenv("BIRDEYE_API_KEY", "").strip()),
            "jupiter_api_key_configured": bool(os.getenv("JUPITER_API_KEY", "").strip()),
        },
        "source_capabilities": capability,
        "incomplete_jobs": {key: value for key, value in jobs.items() if key in {"pending", "running", "partial"}},
        "failed_jobs": jobs.get("failed", 0),
        "request_budget": config.request_budget,
        "run_budget": {
            "max_concurrency": config.max_concurrency,
            "max_retries": config.max_retries,
            "max_pages_per_job": config.max_pages_per_job,
        },
        "ready_for_source_backfill": ready,
        "blocked_reasons": sorted(set(blocked)),
        "degraded_reasons": sorted(set(degraded)),
        "recommended_command": (
            "python -m research.cli plan-backfill --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2"
            if ready
            else "configure HELIUS_API_KEY or HELIUS_RPC_URL for historical backfill"
        ),
    }

from __future__ import annotations

import argparse
import json
from pathlib import Path
from dataclasses import replace
from typing import Any

from research.backfill.jobs import run_fixture_backfill
from research.capabilities import probe_source_capabilities
from research.config import ResearchConfig, load_config
from research.doctor import run_doctor
from research.features.snapshots import build_fixture_snapshots
from research.matching.controls import build_fixture_controls
from research.modes import ResearchModeError, ensure_mode_allows_fixture, resolve_mode
from research.outcomes.labels import build_fixture_outcomes
from research.registry import register_benchmark_names, validate_operator_seeds
from research.replay.action_replay import run_fixture_action_replay
from research.reports.static import generate_static_report
from research.source_pipeline import (
    build_source_controls,
    build_source_features,
    build_source_outcomes,
    generate_source_report,
    plan_source_backfill,
    run_source_action_replay,
    run_source_backfill,
    validate_operator_seeds_source,
)
from research.storage import ResearchStore


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _write_source_pilot_artifact(config: ResearchConfig, name: str, payload: dict[str, Any]) -> None:
    if config.mode != "source":
        return
    out = config.artifact_dir / "real_historical_pilot"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(_json(payload), encoding="utf-8")


def _config_from_args(args: argparse.Namespace, *, command: str | None = None) -> ResearchConfig:
    config = load_config()
    mode = resolve_mode(getattr(args, "mode", None), command=command, require_explicit_for_mutation=True)
    config = replace(config, mode=mode)
    if getattr(args, "artifact_dir", None):
        config = replace(config, artifact_dir=Path(args.artifact_dir))
    if getattr(args, "random_seed", None) is not None:
        config = replace(config, random_seed=int(args.random_seed))
    if getattr(args, "request_budget", None) is not None:
        config = replace(config, request_budget=int(args.request_budget))
    if getattr(args, "concurrency", None) is not None:
        config = replace(config, max_concurrency=int(args.concurrency))
    if getattr(args, "max_pages", None) is not None:
        config = replace(config, max_pages_per_job=int(args.max_pages))
    return config


def status(config: ResearchConfig) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    with store.connect() as conn:
        token_count = conn.execute("SELECT COUNT(*) AS c FROM research_tokens").fetchone()["c"]
        seed_solana = conn.execute("SELECT COUNT(*) AS c FROM research_tokens WHERE canonical_chain='solana'").fetchone()["c"]
        seed_evm = conn.execute("SELECT COUNT(*) AS c FROM research_tokens WHERE canonical_chain='evm'").fetchone()["c"]
        job_rows = conn.execute("SELECT status, COUNT(*) AS c FROM research_jobs GROUP BY status").fetchall()
        snapshots = conn.execute("SELECT COUNT(*) AS c FROM research_snapshots").fetchone()["c"]
        outcomes = conn.execute("SELECT COUNT(*) AS c FROM research_outcomes").fetchone()["c"]
        matches = conn.execute("SELECT COUNT(*) AS c FROM research_matches").fetchone()["c"]
        replays = conn.execute("SELECT COUNT(*) AS c FROM research_action_replays").fetchone()["c"]
        source_snapshots = conn.execute("SELECT COUNT(*) AS c FROM research_snapshots WHERE data_mode='source'").fetchone()["c"]
        fixture_snapshots = conn.execute("SELECT COUNT(*) AS c FROM research_snapshots WHERE data_mode='fixture'").fetchone()["c"]
        source_outcomes = conn.execute("SELECT COUNT(*) AS c FROM research_outcomes WHERE data_mode='source'").fetchone()["c"]
        fixture_outcomes = conn.execute("SELECT COUNT(*) AS c FROM research_outcomes WHERE data_mode='fixture'").fetchone()["c"]
        real_tokens = conn.execute("SELECT COUNT(DISTINCT token) AS c FROM research_parquet_files WHERE data_mode='source' AND token IS NOT NULL").fetchone()["c"]
        fixture_tokens = conn.execute("SELECT COUNT(DISTINCT token_id) AS c FROM research_snapshots WHERE data_mode='fixture'").fetchone()["c"]
        source_replays = conn.execute("SELECT COUNT(*) AS c FROM research_action_replays WHERE data_mode='source'").fetchone()["c"]
        fixture_replays = conn.execute("SELECT COUNT(*) AS c FROM research_action_replays WHERE data_mode='fixture'").fetchone()["c"]
        latest_source_run = conn.execute("SELECT MAX(updated_ts) AS ts FROM research_jobs WHERE data_mode='source'").fetchone()["ts"]
        latest_fixture_run = conn.execute("SELECT MAX(updated_ts) AS ts FROM research_jobs WHERE data_mode='fixture'").fetchone()["ts"]
    jobs = {row["status"]: row["c"] for row in job_rows}
    return {
        "data_mode": config.mode,
        "db_path": str(config.db_path),
        "artifact_dir": str(config.artifact_dir),
        "research_data_dir": str(config.data_dir),
        "real_token_count": real_tokens,
        "fixture_token_count": fixture_tokens,
        "operator_seed_tokens": token_count,
        "operator_seed_solana": seed_solana,
        "operator_seed_evm": seed_evm,
        "jobs": jobs,
        "snapshots": snapshots,
        "source_backed_snapshots": source_snapshots,
        "fixture_snapshots": fixture_snapshots,
        "outcomes": outcomes,
        "source_backed_outcomes": source_outcomes,
        "fixture_outcomes": fixture_outcomes,
        "matched_controls": matches,
        "action_replays": replays,
        "source_action_replays": source_replays,
        "fixture_action_replays": fixture_replays,
        "latest_source_run": latest_source_run,
        "latest_fixture_run": latest_fixture_run,
        "next_required_step": _next_step(token_count, snapshots, outcomes, matches, replays),
    }


def _next_step(token_count: int, snapshots: int, outcomes: int, matches: int, replays: int) -> str:
    if token_count == 0:
        return "validate-seeds"
    if snapshots == 0:
        return "build-features"
    if outcomes == 0:
        return "build-outcomes"
    if matches < 60:
        return "build-controls"
    if replays == 0:
        return "replay-actions"
    return "report"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research.cli", description="Signal Engine offline memecoin research corpus")
    parser.add_argument("--artifact-dir", default=None)
    parser.add_argument("--random-seed", type=int, default=None)
    parser.add_argument("--mode", choices=["source", "fixture", "hybrid"], default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in (
        "capabilities",
        "doctor",
        "validate-seeds",
        "discover-winners",
        "plan-backfill",
        "build-controls",
        "build-features",
        "build-outcomes",
        "replay-actions",
        "report",
        "status",
        "resume",
    ):
        cmd = sub.add_parser(name)
        cmd.add_argument("--mode", choices=["source", "fixture", "hybrid"], default=None)
        cmd.add_argument("--token", default=None)
        cmd.add_argument("--token-limit", type=int, default=None)
        cmd.add_argument("--max-tokens", type=int, default=None)
        cmd.add_argument("--start", default=None)
        cmd.add_argument("--end", default=None)
        cmd.add_argument("--source", default=None)
        cmd.add_argument("--dry-run", action="store_true")
        cmd.add_argument("--force-refresh", action="store_true")
        cmd.add_argument("--resume", action="store_true")
        cmd.add_argument("--concurrency", type=int, default=2)
        cmd.add_argument("--request-budget", type=int, default=None)
        cmd.add_argument("--max-pages", type=int, default=None)
        cmd.add_argument("--max-records", type=int, default=None)
        cmd.add_argument("--cohort", default="operator_seed_cohort_v1")

    backfill = sub.add_parser("backfill")
    backfill.add_argument("--mode", choices=["source", "fixture", "hybrid"], default=None)
    backfill.add_argument("--cohort", default="operator_seed_cohort_v1")
    backfill.add_argument("--token", default=None)
    backfill.add_argument("--token-limit", type=int, default=None)
    backfill.add_argument("--max-tokens", type=int, default=None)
    backfill.add_argument("--start", default=None)
    backfill.add_argument("--end", default=None)
    backfill.add_argument("--source", default=None)
    backfill.add_argument("--dry-run", action="store_true")
    backfill.add_argument("--force-refresh", action="store_true")
    backfill.add_argument("--resume", action="store_true")
    backfill.add_argument("--concurrency", type=int, default=2)
    backfill.add_argument("--request-budget", type=int, default=None)
    backfill.add_argument("--max-pages", type=int, default=None)
    backfill.add_argument("--max-records", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    try:
        config = _config_from_args(args, command=command)
    except ResearchModeError as exc:
        print(_json({"status": "blocked", "error": str(exc), "recommended_command": f"python -m research.cli {command} --mode source"}))
        return 2

    if command == "capabilities":
        payload = probe_source_capabilities(config)
        payload["data_mode"] = config.mode
        _write_source_pilot_artifact(config, "capabilities.json", payload)
    elif command == "doctor":
        payload = run_doctor(config)
        _write_source_pilot_artifact(config, "doctor.json", payload)
    elif command == "validate-seeds":
        payload = validate_operator_seeds_source(config) if config.mode == "source" else validate_operator_seeds(config)
        payload["data_mode"] = config.mode
    elif command == "discover-winners":
        payload = {
            "status": "registered_seed_names_only",
            "benchmark_candidates": register_benchmark_names(config),
            "next_required_step": "configure historical sources and run source-specific discovery",
            "no_survivorship_bias_guard": True,
            "data_mode": config.mode,
        }
    elif command == "plan-backfill":
        payload = plan_source_backfill(config, token=args.token, cohort=args.cohort, sources=args.source, max_tokens=args.max_tokens or args.token_limit)
    elif command == "backfill":
        if config.mode == "source":
            payload = run_source_backfill(config, token=args.token, cohort=args.cohort, sources=args.source, request_budget=args.request_budget, max_pages=args.max_pages, max_records=args.max_records, max_tokens=args.max_tokens or args.token_limit, resume=args.resume)
        else:
            ensure_mode_allows_fixture(config.mode)
            result = run_fixture_backfill(config, cohort=args.cohort, limit=args.token_limit, dry_run=args.dry_run)
            payload = {**result.__dict__, "data_mode": "fixture"}
    elif command == "build-features":
        payload = build_source_features(config, token=args.token) if config.mode == "source" else {**build_fixture_snapshots(config), "data_mode": "fixture"}
    elif command == "build-outcomes":
        payload = build_source_outcomes(config, token=args.token) if config.mode == "source" else {**build_fixture_outcomes(config), "data_mode": "fixture"}
    elif command == "build-controls":
        payload = build_source_controls(config, token=args.token) if config.mode == "source" else {**build_fixture_controls(config), "data_mode": "fixture"}
    elif command == "replay-actions":
        payload = run_source_action_replay(config, token=args.token, limit=args.token_limit) if config.mode == "source" else {**run_fixture_action_replay(config, limit=args.token_limit), "data_mode": "fixture"}
    elif command == "report":
        payload = generate_source_report(config, token=args.token) if config.mode == "source" else generate_static_report(config)
        payload["data_mode"] = config.mode
    elif command == "resume":
        current = status(config)
        next_step = current["next_required_step"]
        payload = {"status": current, "resume_command": f"python -m research.cli {next_step} --mode {config.mode}"}
    elif command == "status":
        payload = status(config)
    else:
        parser.error(f"unsupported command {command}")
        return 2
    print(_json(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

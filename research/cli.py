from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research.backfill.jobs import run_fixture_backfill
from research.capabilities import probe_source_capabilities
from research.config import ResearchConfig, load_config
from research.features.snapshots import build_fixture_snapshots
from research.matching.controls import build_fixture_controls
from research.outcomes.labels import build_fixture_outcomes
from research.registry import register_benchmark_names, validate_operator_seeds
from research.replay.action_replay import run_fixture_action_replay
from research.reports.static import generate_static_report
from research.storage import ResearchStore


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _config_from_args(args: argparse.Namespace) -> ResearchConfig:
    config = load_config()
    if getattr(args, "artifact_dir", None):
        config = ResearchConfig(
            db_path=config.db_path,
            data_dir=config.data_dir,
            artifact_dir=Path(args.artifact_dir),
            random_seed=config.random_seed,
        )
    if getattr(args, "random_seed", None) is not None:
        config = ResearchConfig(
            db_path=config.db_path,
            data_dir=config.data_dir,
            artifact_dir=config.artifact_dir,
            random_seed=int(args.random_seed),
        )
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
    jobs = {row["status"]: row["c"] for row in job_rows}
    return {
        "db_path": str(config.db_path),
        "artifact_dir": str(config.artifact_dir),
        "research_data_dir": str(config.data_dir),
        "operator_seed_tokens": token_count,
        "operator_seed_solana": seed_solana,
        "operator_seed_evm": seed_evm,
        "jobs": jobs,
        "snapshots": snapshots,
        "outcomes": outcomes,
        "matched_controls": matches,
        "action_replays": replays,
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
    sub = parser.add_subparsers(dest="command", required=True)

    for name in (
        "capabilities",
        "validate-seeds",
        "discover-winners",
        "build-controls",
        "build-features",
        "build-outcomes",
        "replay-actions",
        "report",
        "status",
        "resume",
    ):
        cmd = sub.add_parser(name)
        cmd.add_argument("--token-limit", type=int, default=None)
        cmd.add_argument("--start", default=None)
        cmd.add_argument("--end", default=None)
        cmd.add_argument("--source", default=None)
        cmd.add_argument("--dry-run", action="store_true")
        cmd.add_argument("--force-refresh", action="store_true")
        cmd.add_argument("--resume", action="store_true")
        cmd.add_argument("--concurrency", type=int, default=2)
        cmd.add_argument("--cohort", default="operator_seed_cohort_v1")

    backfill = sub.add_parser("backfill")
    backfill.add_argument("--cohort", default="operator_seed_cohort_v1")
    backfill.add_argument("--token-limit", type=int, default=None)
    backfill.add_argument("--start", default=None)
    backfill.add_argument("--end", default=None)
    backfill.add_argument("--source", default=None)
    backfill.add_argument("--dry-run", action="store_true")
    backfill.add_argument("--force-refresh", action="store_true")
    backfill.add_argument("--resume", action="store_true")
    backfill.add_argument("--concurrency", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _config_from_args(args)
    command = args.command

    if command == "capabilities":
        payload = probe_source_capabilities(config)
    elif command == "validate-seeds":
        payload = validate_operator_seeds(config)
    elif command == "discover-winners":
        payload = {
            "status": "registered_seed_names_only",
            "benchmark_candidates": register_benchmark_names(config),
            "next_required_step": "configure historical sources and run source-specific discovery",
            "no_survivorship_bias_guard": True,
        }
    elif command == "backfill":
        result = run_fixture_backfill(config, cohort=args.cohort, limit=args.token_limit, dry_run=args.dry_run)
        payload = result.__dict__
    elif command == "build-features":
        payload = build_fixture_snapshots(config)
    elif command == "build-outcomes":
        payload = build_fixture_outcomes(config)
    elif command == "build-controls":
        payload = build_fixture_controls(config)
    elif command == "replay-actions":
        payload = run_fixture_action_replay(config, limit=args.token_limit)
    elif command == "report":
        payload = generate_static_report(config)
    elif command == "resume":
        current = status(config)
        next_step = current["next_required_step"]
        payload = {"status": current, "resume_command": f"python -m research.cli {next_step}"}
    elif command == "status":
        payload = status(config)
    else:
        parser.error(f"unsupported command {command}")
        return 2
    print(_json(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

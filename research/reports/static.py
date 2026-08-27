from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from research.config import ResearchConfig
from research.storage import ResearchStore


def _write_json(config: ResearchConfig, name: str, payload: dict[str, Any]) -> None:
    config.artifact_dir.mkdir(parents=True, exist_ok=True)
    (config.artifact_dir / name).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def generate_static_report(config: ResearchConfig) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    with store.connect() as conn:
        tokens = conn.execute("SELECT * FROM research_tokens ORDER BY supplied_address").fetchall()
        jobs = conn.execute("SELECT status, COUNT(*) AS c FROM research_jobs GROUP BY status").fetchall()
        snapshots = conn.execute("SELECT COUNT(*) AS c FROM research_snapshots").fetchone()["c"]
        outcomes = conn.execute("SELECT COUNT(*) AS c FROM research_outcomes").fetchone()["c"]
        matches = conn.execute("SELECT COUNT(*) AS c FROM research_matches").fetchone()["c"]
        replays = conn.execute("SELECT * FROM research_action_replays").fetchall()
        capabilities = conn.execute("SELECT source, payload_json FROM research_source_capabilities ORDER BY source").fetchall()

    chain_counts = Counter(row["canonical_chain"] for row in tokens)
    validation = [
        {
            "supplied_address": row["supplied_address"],
            "chain": row["canonical_chain"],
            "canonical_address": row["canonical_address"],
            "symbol": row["symbol"],
            "name": row["name"],
            "creation_ts": row["creation_ts"],
            "launchpad": row["launchpad"],
            "verification_status": row["verification_status"],
            "validation_status": row["validation_status"],
        }
        for row in tokens
    ]
    action_counts: Counter[str] = Counter()
    profile_counts: Counter[str] = Counter()
    for row in replays:
        profile_counts[row["profile"]] += 1
        for action in json.loads(row["actions_json"]):
            action_counts[action["action"]] += 1

    cohort_summary = {
        "operator_seed_count": len(tokens),
        "chain_counts": dict(chain_counts),
        "tokens": validation,
        "fixture_pilot": {
            "snapshots": snapshots,
            "outcomes": outcomes,
            "matches": matches,
            "action_replays": len(replays),
            "quality": "fixture_only_until historical sources are configured and backfilled",
        },
    }
    _write_json(config, "cohort_summary.json", cohort_summary)

    action_summary = {
        "replay_version": "current-action-engine-replay-v1",
        "replay_rows": len(replays),
        "action_counts": dict(action_counts),
        "profile_counts": dict(profile_counts),
        "limitations": [
            "fixture-only pilot does not validate profitability",
            "historical execution remains unavailable until source backfill completes",
            "operator seeds are evaluation examples, not threshold tuning data",
        ],
    }
    _write_json(config, "action_replay_summary.json", action_summary)

    source_payloads = [json.loads(row["payload_json"]) for row in capabilities]
    fee_study = {
        "status": "pending_real_backfill",
        "questions": [
            "organic fee SOL versus total fee SOL",
            "independent fee-payer clusters",
            "failed-transaction fee spam",
            "fee-to-holder and fee-to-liquidity conversion",
        ],
        "coverage": source_payloads,
    }
    catalyst_study = {
        "status": "pending_real_backfill",
        "states": ["RUMOR", "UNVERIFIED", "VERIFIED", "ACTIVE", "FLOW_CONFIRMED", "HIGH_CONVICTION", "PRICED_IN", "WEAKENING", "INVALIDATED", "EXPIRED", "FALSE_OR_RETRACTED"],
        "fixture_only": True,
    }
    matched_control_study = {
        "status": "fixture_pilot_complete" if matches >= 60 else "partial_fixture_pilot",
        "matches": matches,
        "method": "pre-outcome deterministic nearest bucket",
        "no_future_matching_variables": True,
    }
    _write_json(config, "fee_commitment_study.json", fee_study)
    _write_json(config, "catalyst_study.json", catalyst_study)
    _write_json(config, "matched_control_study.json", matched_control_study)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Signal Engine Research Corpus</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #17202a; background: #f7f8fa; }}
    main {{ max-width: 1120px; margin: 0 auto; }}
    section {{ background: #fff; border: 1px solid #d9dee7; border-radius: 6px; padding: 18px; margin: 14px 0; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e6e9ef; text-align: left; padding: 7px; }}
    .warn {{ color: #8a4b00; font-weight: 700; }}
    code {{ background: #eef1f5; padding: 2px 4px; border-radius: 3px; }}
  </style>
</head>
<body>
<main>
  <h1>Solana Memecoin Research Corpus V1</h1>
  <section>
    <h2>Status</h2>
    <p class="warn">This artifact is an offline fixture pilot until real historical sources are configured and backfilled. Missing fields are not treated as zero.</p>
    <p>Operator seeds: {len(tokens)}. Snapshots: {snapshots}. Outcomes: {outcomes}. Matched controls: {matches}. Action replays: {len(replays)}.</p>
  </section>
  <section>
    <h2>Operator Seed Validation</h2>
    <table><thead><tr><th>Address</th><th>Chain</th><th>Symbol</th><th>Name</th><th>Status</th></tr></thead><tbody>
    {''.join(f"<tr><td><code>{row['supplied_address']}</code></td><td>{row['canonical_chain']}</td><td>{row['symbol'] or 'unavailable'}</td><td>{row['name'] or 'unavailable'}</td><td>{row['verification_status']}</td></tr>" for row in tokens)}
    </tbody></table>
  </section>
  <section>
    <h2>Source Capabilities</h2>
    <pre>{json.dumps(source_payloads, indent=2)}</pre>
  </section>
  <section>
    <h2>Replay Summary</h2>
    <pre>{json.dumps(action_summary, indent=2)}</pre>
  </section>
</main>
</body>
</html>
"""
    config.artifact_dir.mkdir(parents=True, exist_ok=True)
    (config.artifact_dir / "index.html").write_text(html, encoding="utf-8")

    return {
        "artifact_dir": str(config.artifact_dir),
        "files": [
            "index.html",
            "cohort_summary.json",
            "coverage_matrix.json",
            "action_replay_summary.json",
            "fee_commitment_study.json",
            "catalyst_study.json",
            "matched_control_study.json",
        ],
        "summary": cohort_summary,
    }


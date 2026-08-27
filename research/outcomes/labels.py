from __future__ import annotations

import json
import uuid
from typing import Any

from research.config import ResearchConfig
from research.storage import ResearchStore


OUTCOME_VERSION = "memecoin-outcome-labels-v1"


def target_before_stop(path: list[dict[str, Any]], *, entry_ts: int, target_pct: float, stop_pct: float) -> str:
    ordered = sorted((row for row in path if int(row.get("ts", 0)) >= entry_ts), key=lambda row: int(row.get("ts", 0)))
    if not ordered:
        return "right_censored_missing_price"
    entry = float(ordered[0].get("price") or 0)
    if entry <= 0:
        return "missing_price"
    for row in ordered:
        change = (float(row.get("price") or 0) - entry) / entry * 100.0
        if change <= stop_pct:
            return "stop_before_target"
        if change >= target_pct:
            return "target_before_stop"
    return "insufficient_observation_time"


def excursion_metrics(path: list[dict[str, Any]]) -> dict[str, Any]:
    if not path:
        return {"resolution_status": "missing_price"}
    entry = float(path[0].get("price") or 0)
    if entry <= 0:
        return {"resolution_status": "missing_price"}
    returns = [(float(row.get("price") or 0) - entry) / entry * 100.0 for row in path]
    return {
        "maximum_favorable_excursion_pct": max(returns),
        "maximum_adverse_excursion_pct": min(returns),
        "resolution_status": "resolved",
    }


def build_fixture_outcomes(config: ResearchConfig) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    with store.connect() as conn:
        snapshots = conn.execute("SELECT DISTINCT token_id FROM research_snapshots").fetchall()
        for row in snapshots:
            token_id = row["token_id"]
            fixture_winner = token_id.startswith("fixture-winner")
            labels = {
                "runner_3x": fixture_winner,
                "major_runner_10x": False,
                "elite_runner_25x": False,
                "sustained_winner": fixture_winner,
                "chart_only_winner": False,
                "execution_failed_winner": False,
                "fixture_only": True,
            }
            metrics = {
                "maximum_favorable_excursion_pct": 220.0 if fixture_winner else 18.0,
                "maximum_adverse_excursion_pct": -18.0 if fixture_winner else -55.0,
                "target_reached_before_invalidation": fixture_winner,
                "right_censoring": "fixture_only",
            }
            conn.execute(
                """
                INSERT OR REPLACE INTO research_outcomes
                (outcome_id, token_id, labels_json, metrics_json, resolution_status, outcome_version)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (uuid.uuid5(uuid.NAMESPACE_URL, token_id).hex, token_id, json.dumps(labels, sort_keys=True), json.dumps(metrics, sort_keys=True), "fixture_only", OUTCOME_VERSION),
            )
    return {"outcomes": len(snapshots), "quality": "fixture_only_not_verified"}


from __future__ import annotations

import json
import time
import uuid
from typing import Any

from research.config import ResearchConfig
from research.storage import ResearchStore


SNAPSHOT_OFFSETS = [0, 5, 10, 30, 60, 180, 300, 900, 1800, 3600, 14400, 86400]


def build_snapshot_features(records: list[dict[str, Any]], snapshot_ts: int) -> dict[str, Any]:
    past = [row for row in records if int(row.get("ts", 0)) <= snapshot_ts]
    future = [row for row in records if int(row.get("ts", 0)) > snapshot_ts]
    latest = past[-1] if past else {}
    return {
        "price": {"value": latest.get("price"), "state": "computed" if latest.get("price") is not None else "missing"},
        "liquidity": {"value": latest.get("liquidity"), "state": "computed" if latest.get("liquidity") is not None else "missing"},
        "buy_count": {"value": sum(1 for row in past if row.get("side") == "buy"), "state": "computed"},
        "future_rows_excluded": len(future),
        "missing_is_not_zero": True,
    }


def build_fixture_snapshots(config: ResearchConfig, *, winners: int = 12, controls_per_winner: int = 5) -> dict[str, Any]:
    """Build a deterministic offline pilot for plumbing validation.

    These records are marked `fixture_only`; they are not verified historical
    outcomes and must not be used for threshold tuning.
    """
    store = ResearchStore(config)
    store.init_schema()
    now = int(time.time())
    snapshot_count = 0
    with store.connect() as conn:
        for i in range(winners):
            winner_id = f"fixture-winner-{i:02d}"
            for offset in SNAPSHOT_OFFSETS:
                features = build_snapshot_features(
                    [
                        {"ts": now, "price": 1.0, "liquidity": 10000 + i * 1000, "side": "buy"},
                        {"ts": now + 60, "price": 1.2, "liquidity": 11000 + i * 1000, "side": "buy"},
                    ],
                    now + offset,
                )
                features["fixture_only"] = True
                sid = uuid.uuid5(uuid.NAMESPACE_URL, f"{winner_id}:{offset}").hex
                conn.execute(
                    """
                    INSERT OR REPLACE INTO research_snapshots
                    (snapshot_id, token_id, snapshot_ts, snapshot_label, features_json, quality_json, source_hashes_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sid, winner_id, now + offset, f"t+{offset}s", json.dumps(features, sort_keys=True), json.dumps({"overall": "weak", "fixture_only": True}), "[]"),
                )
                snapshot_count += 1
        for i in range(winners * controls_per_winner):
            control_id = f"fixture-control-{i:03d}"
            sid = uuid.uuid5(uuid.NAMESPACE_URL, f"{control_id}:0").hex
            conn.execute(
                """
                INSERT OR REPLACE INTO research_snapshots
                (snapshot_id, token_id, snapshot_ts, snapshot_label, features_json, quality_json, source_hashes_json)
                VALUES (?, ?, ?, 'creation', ?, ?, '[]')
                """,
                (sid, control_id, now, json.dumps({"fixture_only": True, "missing_is_not_zero": True}, sort_keys=True), json.dumps({"overall": "weak", "fixture_only": True})),
            )
            snapshot_count += 1
    return {"winners": winners, "controls": winners * controls_per_winner, "snapshots": snapshot_count, "quality": "fixture_only_not_threshold_tuning"}


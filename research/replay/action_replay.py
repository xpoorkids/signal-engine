from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import uuid
from typing import Any

from app.services.action_engine_service import ActionEngineService
from research.config import ResearchConfig
from research.execution import reserve_execution_estimate
from research.storage import ResearchStore


REPLAY_VERSION = "current-action-engine-replay-v1"
PROFILES = ["BALANCED", "AGGRESSIVE", "AGGRESSIVE_CATALYST_RUNNER"]
SIZES_USD = [100.0, 250.0, 500.0]


def snapshot_market(features: dict[str, Any], *, intended_size_usd: float) -> dict[str, Any]:
    liquidity = features.get("liquidity", {})
    liquidity_value = liquidity.get("value") if isinstance(liquidity, dict) else None
    estimate = reserve_execution_estimate(size_usd=intended_size_usd, liquidity_usd=liquidity_value)
    price = features.get("price", {})
    return {
        "liquidity_usd": liquidity_value,
        "price_usd": price.get("value") if isinstance(price, dict) else None,
        "buy_impact_pct": estimate.buy_impact_pct,
        "sell_impact_pct": estimate.sell_impact_pct,
        "round_trip_cost_pct": estimate.round_trip_cost_pct,
        "maximum_safe_size_usd": liquidity_value * 0.015 if liquidity_value else 0.0,
        "buy_route_ok": estimate.route_available,
        "sell_route_ok": estimate.route_available,
        "quote_fresh": True,
        "txns_m5_buys": features.get("buy_count", {}).get("value", 0) if isinstance(features.get("buy_count"), dict) else 0,
        "txns_m5_sells": 1,
        "volume_m5": 1000.0 if liquidity_value else 0.0,
        "wallet_or_fee_confirmation": bool(liquidity_value and liquidity_value >= 10000),
        "organic_flow_windows": 2 if liquidity_value and liquidity_value >= 10000 else 0,
        "execution_quality": estimate.quality,
        "historical_replay": True,
        "missing_is_not_zero": features.get("missing_is_not_zero", True),
    }


def replay_token_snapshots(
    snapshots: list[sqlite3.Row],
    *,
    profile: str,
    intended_size_usd: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="action-replay-", ignore_cleanup_errors=True) as tmp:
        engine = ActionEngineService(db_path=f"{tmp}/engine.db")
        actions: list[dict[str, Any]] = []
        simulated_tokens = 0.0
        total_cost = 0.0
        realized = 0.0
        for row in sorted(snapshots, key=lambda item: int(item["snapshot_ts"])):
            features = json.loads(row["features_json"])
            market = snapshot_market(features, intended_size_usd=intended_size_usd)
            token = row["token_id"]
            recommendation = engine.recommend_for_token(token, market=market, intended_size_usd=intended_size_usd, persist=False)
            action = recommendation["action"]
            price = float((features.get("price") or {}).get("value") or 1.0)
            if action in {"BUY NOW", "CATALYST BUY NOW"} and simulated_tokens <= 0:
                simulated_tokens = intended_size_usd / max(price, 0.000001)
                total_cost = intended_size_usd
            elif action in {"BUY SMALL", "CATALYST BUY SMALL"} and simulated_tokens <= 0:
                cost = intended_size_usd * 0.35
                simulated_tokens = cost / max(price, 0.000001)
                total_cost = cost
            elif action in {"TAKE PROFIT", "TRIM", "RECOVER PRINCIPAL", "SELL NOW", "EMERGENCY EXIT"} and simulated_tokens > 0:
                sell_fraction = 1.0 if action in {"SELL NOW", "EMERGENCY EXIT"} else 0.2
                tokens_sold = simulated_tokens * sell_fraction
                realized += tokens_sold * price
                simulated_tokens -= tokens_sold
            actions.append(
                {
                    "snapshot_id": row["snapshot_id"],
                    "snapshot_ts": row["snapshot_ts"],
                    "action": action,
                    "display_action": recommendation.get("display_action"),
                    "profile": profile,
                    "intended_size_usd": intended_size_usd,
                    "market": market,
                    "no_future_information": True,
                }
            )
        last_price = 1.0
        if snapshots:
            final_features = json.loads(sorted(snapshots, key=lambda item: int(item["snapshot_ts"]))[-1]["features_json"])
            last_price = float((final_features.get("price") or {}).get("value") or 1.0)
        unrealized = simulated_tokens * last_price
        return {
            "actions": actions,
            "summary": {
                "profile": profile,
                "intended_size_usd": intended_size_usd,
                "entry_count": sum(1 for item in actions if item["action"] in {"BUY NOW", "BUY SMALL", "CATALYST BUY NOW", "CATALYST BUY SMALL"}),
                "exit_count": sum(1 for item in actions if item["action"] in {"TAKE PROFIT", "TRIM", "RECOVER PRINCIPAL", "SELL NOW", "EMERGENCY EXIT"}),
                "tokens_remaining": simulated_tokens,
                "total_cost_usd": total_cost,
                "realized_usd": realized,
                "unrealized_usd": unrealized,
                "total_executable_pnl_usd": realized + unrealized - total_cost,
                "fixture_only": True,
                "policy_reused": "app.services.action_engine_service.ActionEngineService",
            },
        }


def run_fixture_action_replay(config: ResearchConfig, *, limit: int | None = None) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    created = int(time.time())
    with store.connect() as conn:
        token_rows = conn.execute(
            "SELECT DISTINCT token_id FROM research_snapshots ORDER BY token_id LIMIT ?",
            (limit or 1000,),
        ).fetchall()
        token_ids = [row["token_id"] for row in token_rows]
        replay_count = 0
        for token_id in token_ids:
            snapshots = conn.execute(
                "SELECT * FROM research_snapshots WHERE token_id=? ORDER BY snapshot_ts",
                (token_id,),
            ).fetchall()
            for profile in PROFILES:
                for size in SIZES_USD:
                    result = replay_token_snapshots(snapshots, profile=profile, intended_size_usd=size)
                    replay_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{token_id}:{profile}:{size}:{REPLAY_VERSION}").hex
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO research_action_replays (
                            replay_id, token_id, profile, intended_size_usd, actions_json,
                            summary_json, replay_version, created_ts
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            replay_id,
                            token_id,
                            profile,
                            size,
                            json.dumps(result["actions"], sort_keys=True),
                            json.dumps(result["summary"], sort_keys=True),
                            REPLAY_VERSION,
                            created,
                        ),
                    )
                    replay_count += 1
    return {
        "tokens": len(token_ids),
        "replays": replay_count,
        "profiles": PROFILES,
        "sizes_usd": SIZES_USD,
        "quality": "fixture_only_action_policy_plumbing",
    }

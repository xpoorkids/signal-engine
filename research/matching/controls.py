from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any

from research.config import ResearchConfig
from research.storage import ResearchStore


MATCHING_VERSION = "pre-outcome-exact-bucket-v1"
FORBIDDEN_MATCHING_FIELDS = {
    "future_peak",
    "future_drawdown",
    "outcome",
    "winner_label",
    "eventual_holder_count",
    "later_liquidity",
}


@dataclass(frozen=True)
class MatchCandidate:
    token_id: str
    launchpad: str
    launch_bucket: str
    liquidity_bucket: str
    volume_bucket: str
    holder_bucket: str
    variables: dict[str, Any]


def validate_matching_variables(variables: dict[str, Any]) -> None:
    forbidden = FORBIDDEN_MATCHING_FIELDS.intersection(variables)
    if forbidden:
        raise ValueError(f"future_matching_variable:{sorted(forbidden)[0]}")


def matching_distance(winner: MatchCandidate, control: MatchCandidate) -> float:
    if winner.token_id == control.token_id:
        raise ValueError("winner_cannot_match_itself")
    validate_matching_variables(winner.variables)
    validate_matching_variables(control.variables)
    distance = 0.0
    for attr in ("launchpad", "launch_bucket", "liquidity_bucket", "volume_bucket", "holder_bucket"):
        distance += 0.0 if getattr(winner, attr) == getattr(control, attr) else 1.0
    for key, value in winner.variables.items():
        other = control.variables.get(key)
        if isinstance(value, (int, float)) and isinstance(other, (int, float)):
            scale = max(abs(float(value)), abs(float(other)), 1.0)
            distance += abs(float(value) - float(other)) / scale
    return round(distance, 6)


def select_controls(
    winner: MatchCandidate,
    candidates: list[MatchCandidate],
    *,
    controls_per_winner: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scored: list[tuple[float, MatchCandidate]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.token_id == winner.token_id:
            rejected.append({"token_id": candidate.token_id, "reason": "self_match"})
            continue
        try:
            scored.append((matching_distance(winner, candidate), candidate))
        except ValueError as exc:
            rejected.append({"token_id": candidate.token_id, "reason": str(exc)})
    scored.sort(key=lambda item: (item[0], item[1].token_id))
    selected = [
        {
            "control_token_id": candidate.token_id,
            "match_distance": distance,
            "matching_variables": candidate.variables,
            "reason_selected": "nearest_pre_outcome_bucket",
        }
        for distance, candidate in scored[:controls_per_winner]
    ]
    rejected.extend(
        {"token_id": candidate.token_id, "reason": "not_nearest", "match_distance": distance}
        for distance, candidate in scored[controls_per_winner:]
    )
    return selected, rejected


def _fixture_candidate(token_id: str, index: int, *, winner: bool) -> MatchCandidate:
    return MatchCandidate(
        token_id=token_id,
        launchpad="pump_fun",
        launch_bucket=f"2026w{index % 4}",
        liquidity_bucket=f"liq{index % 3}",
        volume_bucket=f"vol{index % 4}",
        holder_bucket=f"holders{index % 3}",
        variables={
            "token_age_seconds": 60 + index,
            "initial_liquidity_usd": 8_000 + (index % 3) * 2_500,
            "initial_tx_count": 25 + (index % 7),
            "fixture_only": True,
            "class": "winner" if winner else "control",
        },
    )


def build_fixture_controls(config: ResearchConfig, *, winners: int = 12, controls_per_winner: int = 5) -> dict[str, Any]:
    store = ResearchStore(config)
    store.init_schema()
    winner_rows = [_fixture_candidate(f"fixture-winner-{i:02d}", i, winner=True) for i in range(winners)]
    controls = [_fixture_candidate(f"fixture-control-{i:03d}", i, winner=False) for i in range(winners * controls_per_winner)]
    match_count = 0
    now = int(time.time())
    with store.connect() as conn:
        for winner in winner_rows:
            selected, rejected = select_controls(winner, controls, controls_per_winner=controls_per_winner)
            for item in selected:
                match_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{winner.token_id}:{item['control_token_id']}:{MATCHING_VERSION}").hex
                conn.execute(
                    """
                    INSERT OR REPLACE INTO research_matches (
                        match_id, winner_token_id, control_token_id, matching_version,
                        matching_variables_json, match_distance, reason_selected,
                        rejected_alternatives_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        match_id,
                        winner.token_id,
                        item["control_token_id"],
                        MATCHING_VERSION,
                        json.dumps(
                            {
                                "winner": winner.variables,
                                "control": item["matching_variables"],
                                "selected_at": now,
                                "fixture_only": True,
                            },
                            sort_keys=True,
                        ),
                        float(item["match_distance"]),
                        item["reason_selected"],
                        json.dumps(rejected[:20], sort_keys=True),
                    ),
                )
                conn.execute("UPDATE research_matches SET data_mode='fixture' WHERE match_id=?", (match_id,))
                match_count += 1
    expected = winners * controls_per_winner
    return {
        "winners": winners,
        "controls_available": len(controls),
        "matches": match_count,
        "expected_matches": expected,
        "quality": "fixture_only_not_model_training",
        "complete": match_count >= expected,
    }

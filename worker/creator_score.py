from __future__ import annotations

import time
from typing import Dict, Any

from app.services.state_service import get_creator_stats
from worker.signal_policy import creator_score_policy


def compute_creator_score(
    creator: str | None,
    stats: Dict[str, Any] | None = None,
    funded_by_cluster: bool | None = None,
    prior_profitable: bool | None = None,
    wallet_age_days: float | None = None,
) -> Dict[str, Any]:
    policy = creator_score_policy()
    if not creator:
        return {"score": 0.0, "reasons": ["creator_unknown"], "stats": {}}

    if stats is None:
        stats = get_creator_stats(creator)

    deploys_24h = int(stats.get("deploys_24h") or 0)
    deploys_lifetime = int(stats.get("deploys_lifetime") or 0)
    first_seen = int(stats.get("first_seen") or 0)
    now = int(time.time())
    age_days = wallet_age_days
    if age_days is None:
        age_days = (now - first_seen) / 86400 if first_seen else 0.0

    score = policy.base_score
    reasons = []

    if deploys_24h > policy.deploys_24h_penalty_threshold:
        score -= policy.deploys_24h_penalty
        reasons.append(f"deploys_24h>{policy.deploys_24h_penalty_threshold}")
    if deploys_lifetime < policy.deploys_lifetime_penalty_threshold:
        score -= policy.deploys_lifetime_penalty
        reasons.append(f"deploys_lifetime<{policy.deploys_lifetime_penalty_threshold}")
    if funded_by_cluster is True:
        score -= policy.funded_by_cluster_penalty
        reasons.append("funded_by_cluster")

    if prior_profitable is True:
        score += policy.prior_profitable_bonus
        reasons.append("prior_profitable")
    if age_days and age_days > policy.wallet_age_bonus_days:
        score += policy.wallet_age_bonus
        reasons.append(f"wallet_age>{int(policy.wallet_age_bonus_days)}d")
    if (
        deploys_24h <= policy.low_frequency_deploys_24h_max
        and deploys_lifetime <= policy.low_frequency_deploys_lifetime_max
    ):
        score += policy.low_frequency_bonus
        reasons.append("low_deploy_frequency")

    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "reasons": reasons,
        "stats": {
            "deploys_24h": deploys_24h,
            "deploys_lifetime": deploys_lifetime,
            "wallet_age_days": round(age_days or 0.0, 2),
        },
    }

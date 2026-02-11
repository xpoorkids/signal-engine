from __future__ import annotations

import time
from typing import Dict, Any

from app.services.state_service import get_creator_stats


def compute_creator_score(
    creator: str | None,
    stats: Dict[str, Any] | None = None,
    funded_by_cluster: bool | None = None,
    prior_profitable: bool | None = None,
    wallet_age_days: float | None = None,
) -> Dict[str, Any]:
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

    score = 0.5
    reasons = []

    if deploys_24h > 5:
        score -= 0.3
        reasons.append("deploys_24h>5")
    if deploys_lifetime < 2:
        score -= 0.2
        reasons.append("deploys_lifetime<2")
    if funded_by_cluster is True:
        score -= 0.4
        reasons.append("funded_by_cluster")

    if prior_profitable is True:
        score += 0.3
        reasons.append("prior_profitable")
    if age_days and age_days > 30:
        score += 0.2
        reasons.append("wallet_age>30d")
    if deploys_24h <= 1 and deploys_lifetime <= 5:
        score += 0.2
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

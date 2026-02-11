from __future__ import annotations

import time
from typing import Dict, Any

from app.services.state_service import get_creator_stats


def score_creator(creator: str | None, funding_clustered: bool | None = None) -> Dict[str, Any]:
    if not creator:
        return {
            "score": 0.0,
            "reasons": ["creator_unknown"],
            "stats": {},
        }

    stats = get_creator_stats(creator)
    deploys_24h = int(stats.get("deploys_24h") or 0)
    deploys_lifetime = int(stats.get("deploys_lifetime") or 0)
    first_seen = int(stats.get("first_seen") or 0)
    now = int(time.time())
    age_days = (now - first_seen) / 86400 if first_seen else 0.0

    score = 1.0
    reasons = []

    if deploys_24h >= 10:
        score -= 0.5
        reasons.append("deploys_24h>=10")
    elif deploys_24h >= 5:
        score -= 0.3
        reasons.append("deploys_24h>=5")

    if deploys_lifetime >= 100:
        score -= 0.3
        reasons.append("deploys_lifetime>=100")
    elif deploys_lifetime >= 50:
        score -= 0.2
        reasons.append("deploys_lifetime>=50")

    if age_days > 0:
        if age_days < 1:
            score -= 0.4
            reasons.append("wallet_age<1d")
        elif age_days < 7:
            score -= 0.2
            reasons.append("wallet_age<7d")

    if funding_clustered is True:
        score -= 0.2
        reasons.append("funding_clustered")

    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "reasons": reasons,
        "stats": {
            "deploys_24h": deploys_24h,
            "deploys_lifetime": deploys_lifetime,
            "wallet_age_days": round(age_days, 2),
        },
    }

from datetime import datetime, timezone

"""
Signal helpers for watch-stage classification.

Each helper computes a single, deterministic signal from inputs provided
by upstream scanners. Outputs are intentionally lightweight primitives
for use in downstream scoring/classification.
"""

def compute_age_minutes(first_seen_iso: str | None) -> int | None:
    """
    Compute the age of a token in whole minutes.

    Inputs:
    - first_seen_iso: ISO 8601 timestamp string; may include trailing 'Z'

    Outputs:
    - integer minutes since first_seen_iso, rounded down
    - None if input is missing or cannot be parsed

    Assumptions and units:
    - Uses UTC time for "now"
    - Result is in minutes
    """
    if not first_seen_iso:
        return None
    try:
        first_seen = datetime.fromisoformat(first_seen_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return int((now - first_seen).total_seconds() // 60)
    except Exception:
        return None

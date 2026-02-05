from datetime import datetime, timezone

def compute_age_minutes(first_seen_iso: str | None) -> int | None:
    if not first_seen_iso:
        return None
    try:
        first_seen = datetime.fromisoformat(first_seen_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return int((now - first_seen).total_seconds() // 60)
    except Exception:
        return None

import json
from datetime import datetime, timedelta, timezone
from collections import Counter
from typing import Dict, List

WATCH_LOG_PATH = "/data/watch_events.jsonl"

def load_recent_watch_events(hours: int = 24) -> List[Dict]:
    events = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        with open(WATCH_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line)
                    ts = datetime.fromisoformat(event["timestamp"])
                    if ts >= cutoff:
                        events.append(event)
                except Exception:
                    continue
    except FileNotFoundError:
        return []

    return events


def build_watch_summary(hours: int = 24) -> Dict:
    events = load_recent_watch_events(hours)

    if not events:
        return {
            "window_hours": hours,
            "total_watch_events": 0,
            "unique_tokens": 0,
            "top_tokens": [],
            "reason_breakdown": {},
        }

    token_counts = Counter(e["token"] for e in events)
    reason_counts = Counter()

    for e in events:
        for r in e.get("reasons", []):
            reason_counts[r] += 1

    top_tokens = [
        {"token": t, "count": c}
        for t, c in token_counts.most_common(10)
    ]

    return {
        "window_hours": hours,
        "total_watch_events": len(events),
        "unique_tokens": len(token_counts),
        "top_tokens": top_tokens,
        "reason_breakdown": dict(reason_counts),
    }

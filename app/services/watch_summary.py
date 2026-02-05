from collections import Counter
from typing import Dict, List
from app.services.watch_store import load_recent_watch_events as _load_recent_watch_events

def load_recent_watch_events(hours: int = 24) -> List[Dict]:
    """
    Load WATCH events from a JSONL log within a lookback window.

    Inputs:
    - hours: lookback window size in hours (relative to current UTC time)

    Outputs:
    - list of event dicts whose ISO 8601 timestamps are >= cutoff

    Invariants and edge cases:
    - Uses UTC "now" for cutoff calculation
    - Skips malformed JSON lines or events with invalid/missing timestamps
    - Returns [] if the log file does not exist
    - Includes events exactly at the cutoff boundary
    """
    return _load_recent_watch_events(hours)


def build_watch_summary(hours: int = 24) -> Dict:
    """
    Build a summary of recent WATCH activity within the lookback window.

    Outputs:
    - window_hours: the window size in hours
    - total_watch_events: count of events in the window
    - unique_tokens: number of distinct token identifiers
    - top_tokens: top 10 tokens by event count
    - reason_breakdown: frequency of reason strings across events
    """
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

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from app.config import settings

WATCH_LOG_PATH = settings.WATCH_LOG_PATH

def append_watch_event(event: Dict[str, Any]) -> None:
    """
    Append a single WATCH event as JSONL (one JSON object per line).
    Uses /data persistent disk on Render if available.
    """
    os.makedirs(os.path.dirname(WATCH_LOG_PATH), exist_ok=True)

    # Ensure a timestamp exists
    if "timestamp" not in event:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()

    with open(WATCH_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

def load_recent_watch_events(hours: int = 24) -> list[Dict]:
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

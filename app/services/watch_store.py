import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

WATCH_LOG_PATH = os.getenv("WATCH_LOG_PATH", "/data/watch_events.jsonl")

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

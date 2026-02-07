import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import worker.scanner as scanner


DB_PATH = Path("state/engine.db")


def _parse_from(value: str) -> int:
    v = value.strip()
    if len(v) == 10:
        v = f"{v}T00:00:00+00:00"
    v = v.replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_ts", required=True)
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    start_ts = _parse_from(args.from_ts)
    if not DB_PATH.exists():
        raise SystemExit("state/engine.db not found")

    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            """
            SELECT token, last_metrics, last_seen
            FROM token_state
            WHERE last_seen >= ?
            ORDER BY last_seen ASC
        """,
            (start_ts,),
        ).fetchall()

    for token, last_metrics, last_seen in rows:
        try:
            metrics = json.loads(last_metrics) if last_metrics else {}
        except Exception:
            metrics = {}

        candidate = {
            "token": token,
            "symbol": "REPLAY",
            "reason": "replay",
            "metrics": metrics,
            "observed_at": datetime.fromtimestamp(last_seen, timezone.utc).isoformat(),
            "source": "replay",
        }
        scanner.process_candidate(candidate)
        if args.sleep > 0:
            time.sleep(args.sleep)


if __name__ == "__main__":
    main()

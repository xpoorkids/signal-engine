"""
scan_replay_test.py

Replays historical watch.log events through the current signal engine.

Purpose:
- Detect regressions in stage transitions
- Validate cooldown + hysteresis on real data
- Read-only (DRY_RUN enforced)

Run:
  python -m tests.scan_replay_test
"""

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.watch.watch_state_manager import evolve_watch_stage
from app.watch.stage_config import SOL_STAGE_THRESHOLDS as T

WATCH_LOG_PATH = os.getenv(
    "SIGNAL_ENGINE_REPLAY_PATH",
    str(Path(__file__).parent / "fixtures" / "watch_replay.jsonl"),
)
DRY_RUN = True


def load_events(path: str) -> list[dict]:
    events: list[dict] = []
    if not os.path.exists(path):
        return events
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.min
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_signals_from_event(e: dict) -> dict:
    """
    Best-effort reconstruction.
    Missing fields are filled conservatively.
    """
    return {
        "token": e.get("token", "unknown"),
        "chain": e.get("chain", "solana"),
        "lp_usd": e.get("lp_usd", 0),
        "vol_5m": e.get("vol_5m", 0),
        "tx_5m": e.get("tx_5m", 0),
        "holders_delta_15m": e.get("holders_delta_15m", 0),
        "top10_pct": e.get("top10_pct", 100),
        "rug_bad": e.get("rug_bad", False),
        "ts": _parse_ts(e.get("ts") or e.get("timestamp")),
        "reason": e.get("reason", "replay"),
    }


def main() -> None:
    events = load_events(WATCH_LOG_PATH)
    if not events:
        raise AssertionError(f"Replay fixture missing or empty: {WATCH_LOG_PATH}")

    by_token = defaultdict(list)
    for e in events:
        token = e.get("token")
        if not token:
            continue
        by_token[token].append(e)

    # NOTE: cooldown is enforced in ticks (promotion_cooldown_ticks),
    # not wall-clock time. Replay mirrors engine semantics intentionally.
    promotions = defaultdict(int)
    demotions = defaultdict(int)
    cooldown_violations: list[str] = []
    prev_stage: dict[str, str | None] = {}
    last_promo_tick: dict[str, int] = {}
    cooldown_ticks = int(T["near_pass_confirmation"]["promotion_cooldown_ticks"])
    processed_events = 0
    transition_count = 0

    print("\n=== REPLAY TEST ===")

    for token, rows in by_token.items():
        rows.sort(key=lambda r: _parse_ts(r.get("ts") or r.get("timestamp")))
        prev_stage[token] = None

        for idx, r in enumerate(rows, start=1):
            signals = build_signals_from_event(r)
            result = evolve_watch_stage(signals, dry_run=DRY_RUN)
            processed_events += 1

            if prev_stage[token] is None:
                prev_stage[token] = result.stage
                continue

            if result.stage == prev_stage[token]:
                continue

            from_stage = prev_stage[token]
            to_stage = result.stage
            ts = signals["ts"]
            transition_count += 1

            print(
                f"[REPLAY] {token:<12} {from_stage:<10} {to_stage:<10} {ts.isoformat()}"
            )

            if to_stage == "near_pass":
                promotions[token] += 1
                last = last_promo_tick.get(token)
                if last is not None and (idx - last) <= cooldown_ticks:
                    cooldown_violations.append(
                        f"{token} promoted at tick {idx} within cooldown"
                    )
                last_promo_tick[token] = idx

            if from_stage == "near_pass" and to_stage != "near_pass":
                demotions[token] += 1

            prev_stage[token] = result.stage

    errors: list[str] = []

    if cooldown_violations:
        errors.append(f"Cooldown violations: {cooldown_violations}")
    if processed_events < 4:
        errors.append(f"Replay processed too few events: {processed_events}")
    if transition_count < 1:
        errors.append("Replay produced no stage transitions")

    print("\n=== RESULT ===")
    if errors:
        print(" FAIL")
        for e in errors:
            print(f" - {e}")
        raise AssertionError("Replay test failed")
    else:
        print(" PASS")


if __name__ == "__main__":
    main()

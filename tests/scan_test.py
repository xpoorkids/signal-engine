"""
scan_test.py

Deterministic end-to-end scan test for signal-engine.

Purpose:
- Prove scan  classify  stage transitions
- Exercise near-pass confirmation logic
- Write to watch.log
- Print human-readable results

NO webhooks
NO n8n
NO threading
"""

import os
import time
from datetime import datetime, timezone
from collections import defaultdict

from app.watch.watch_state_manager import evolve_watch_stage
from app.watch.stage_config import SOL_STAGE_THRESHOLDS as T

DRY_RUN = os.getenv("DRY_RUN", "1").strip().lower() in {"1", "true", "yes", "on"}

SIGNAL_SCRIPT = {
    "CONFIRM_OK": [
        {"vol_5m": 18000, "tx_5m": 60, "holders_delta_15m": 5, "top10_pct": 38},
        {"vol_5m": 60000, "tx_5m": 140, "holders_delta_15m": 25, "top10_pct": 35},
        {"vol_5m": 72000, "tx_5m": 170, "holders_delta_15m": 55, "top10_pct": 34},
        {"vol_5m": 70000, "tx_5m": 150, "holders_delta_15m": 60, "top10_pct": 35},
        {"vol_5m": 65000, "tx_5m": 140, "holders_delta_15m": 50, "top10_pct": 36},
        {"vol_5m": 2000, "tx_5m": 8, "holders_delta_15m": 2, "top10_pct": 45},
        {"vol_5m": 80000, "tx_5m": 180, "holders_delta_15m": 60, "top10_pct": 36},
        {"vol_5m": 28000, "tx_5m": 70, "holders_delta_15m": 8, "top10_pct": 42},
        {"vol_5m": 80000, "tx_5m": 170, "holders_delta_15m": 60, "top10_pct": 36},
        {"vol_5m": 82000, "tx_5m": 190, "holders_delta_15m": 70, "top10_pct": 35},
    ],
    "FLAP_FAIL": [
        {"vol_5m": 70000, "tx_5m": 150, "holders_delta_15m": 18, "top10_pct": 45},
        {"vol_5m": 45000, "tx_5m": 90, "holders_delta_15m": 6, "top10_pct": 46},
        {"vol_5m": 72000, "tx_5m": 155, "holders_delta_15m": 19, "top10_pct": 44},
        {"vol_5m": 48000, "tx_5m": 95, "holders_delta_15m": 7, "top10_pct": 45},
    ],
}

TICKS = max(len(steps) for steps in SIGNAL_SCRIPT.values())
TICK_INTERVAL_SEC = 1

_STAGE_LABELS = {
    "early": "EARLY",
    "building": "WATCH",
    "near_pass": "NEAR_PASS",
}


def run_scan_test():
    print("\n=== SIGNAL ENGINE SCAN TEST ===\n")

    prev_stage: dict[str, str | None] = {token: None for token in SIGNAL_SCRIPT}
    promotion_count: dict[str, int] = defaultdict(int)
    demotion_count: dict[str, int] = defaultdict(int)
    first_promo_tick: dict[str, int] = {}
    cooldown_violations: list[str] = []
    last_promo_tick: dict[str, int] = {}
    cooldown_ticks = int(T["near_pass_confirmation"]["promotion_cooldown_ticks"])
    history = defaultdict(list)

    for tick in range(TICKS):
        tick_num = tick + 1
        print(f"\n--- TICK {tick_num} ---")

        for token, steps in SIGNAL_SCRIPT.items():
            idx = min(tick, len(steps) - 1)
            step = steps[idx]
            now = datetime.now(timezone.utc)
            signals = {
                "token": token,
                "chain": "solana",
                "lp_usd": 120_000,
                "vol_5m": step["vol_5m"],
                "tx_5m": step["tx_5m"],
                "holders_delta_15m": step["holders_delta_15m"],
                "top10_pct": step["top10_pct"],
                "rug_bad": False,
                "ts": now,
            }

            decision = evolve_watch_stage(signals, dry_run=DRY_RUN)

            if prev_stage[token] is None:
                prev_stage[token] = decision.stage
            elif decision.stage != prev_stage[token]:
                from_stage = prev_stage[token]
                to_stage = decision.stage
                before = _STAGE_LABELS.get(from_stage, from_stage.upper())
                after = _STAGE_LABELS.get(to_stage, to_stage.upper())
                print(f"[T{tick_num:02d}] {token:<12} {before:<9} {after}")
                if to_stage == "near_pass":
                    promotion_count[token] += 1
                    if token not in first_promo_tick:
                        first_promo_tick[token] = tick_num
                    last = last_promo_tick.get(token)
                    if last is not None and (tick_num - last) <= cooldown_ticks:
                        cooldown_violations.append(f"{token} promoted at tick {tick_num} within cooldown")
                    last_promo_tick[token] = tick_num
                if from_stage == "near_pass" and to_stage != "near_pass":
                    demotion_count[token] += 1
                prev_stage[token] = decision.stage

            history[token].append({
                "tick": tick_num,
                "score": decision.score,
                "reason": decision.reasons,
                "stage": decision.stage,
            })

            print(
                f"{token:<10} "
                f"score={decision.score:.2f} "
                f"stage={decision.stage:<10} "
                f"reason={decision.reasons}"
            )

        if not DRY_RUN:
            time.sleep(TICK_INTERVAL_SEC)

    print("\n=== SUMMARY ===\n")
    for token, events in history.items():
        stages = [e["stage"] for e in events]
        print(f"{token}: {stages}")

    errors = []

    if promotion_count["CONFIRM_OK"] != 2:
        errors.append("CONFIRM_OK should promote exactly twice")

    if demotion_count["CONFIRM_OK"] != 1:
        errors.append("CONFIRM_OK should demote exactly once")

    if first_promo_tick.get("CONFIRM_OK", 999) > 4:
        errors.append("CONFIRM_OK first promotion should occur by tick 4")

    if promotion_count.get("FLAP_FAIL", 0) != 0:
        errors.append("FLAP_FAIL should never promote")

    if cooldown_violations:
        errors.append("Cooldown violation detected")

    print("\n=== RESULT ===")
    if errors:
        print(" FAIL")
        for e in errors:
            print(f" - {e}")
        raise AssertionError("Scan test failed")
    else:
        print(" PASS")


if __name__ == "__main__":
    run_scan_test()

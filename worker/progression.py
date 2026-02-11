from __future__ import annotations

from typing import Dict, Any, Tuple, List


def _pct_improve(prev: float, curr: float, pct: float) -> bool:
    if curr <= prev:
        return False
    if prev <= 0:
        return True
    return (curr - prev) / prev >= pct


def metrics_improved(
    prev: Dict[str, Any],
    curr: Dict[str, Any],
    pct_threshold: float,
) -> Tuple[bool, List[str]]:
    keys = [
        ("attention_score", "attention"),
        ("unique_buyers_5m", "buyers"),
        ("liquidity", "liquidity"),
        ("score", "score"),
    ]
    improved = []
    for key, label in keys:
        try:
            prev_v = float(prev.get(key) or 0)
            curr_v = float(curr.get(key) or 0)
        except Exception:
            continue
        if _pct_improve(prev_v, curr_v, pct_threshold):
            improved.append(label)
    return (len(improved) > 0), improved

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
    attention_delta: float,
    buyer_delta: float,
    liq_pct: float,
    score_delta: float,
) -> Tuple[bool, List[str]]:
    improved = []
    try:
        if float(curr.get("attention_score") or 0) - float(prev.get("attention_score") or 0) >= attention_delta:
            improved.append("attention")
    except Exception:
        pass
    try:
        if float(curr.get("unique_buyers_5m") or 0) - float(prev.get("unique_buyers_5m") or 0) >= buyer_delta:
            improved.append("buyers")
    except Exception:
        pass
    try:
        if _pct_improve(float(prev.get("liquidity") or 0), float(curr.get("liquidity") or 0), liq_pct):
            improved.append("liquidity")
    except Exception:
        pass
    try:
        if float(curr.get("score") or 0) - float(prev.get("score") or 0) >= score_delta:
            improved.append("score")
    except Exception:
        pass
    return (len(improved) > 0), improved

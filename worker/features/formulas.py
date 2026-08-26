from __future__ import annotations

import math
from collections.abc import Iterable


EPSILON = 1e-9
FEATURE_VERSION = "signal_engine_v2_formulas@1"


def safe_ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    numerator_f = float(numerator)
    denominator_f = float(denominator)
    if not math.isfinite(numerator_f) or not math.isfinite(denominator_f):
        raise ValueError("ratio inputs must be finite")
    if abs(denominator_f) <= EPSILON:
        return None
    return numerator_f / denominator_f


def bounded_order_flow_imbalance(
    buy_notional: float | int | None,
    sell_notional: float | int | None,
    *,
    epsilon: float = EPSILON,
) -> float | None:
    if buy_notional is None or sell_notional is None:
        return None
    buy = float(buy_notional)
    sell = float(sell_notional)
    if buy < 0 or sell < 0:
        raise ValueError("notional values must be non-negative")
    if not math.isfinite(buy) or not math.isfinite(sell):
        raise ValueError("notional values must be finite")
    denominator = max(buy + sell, epsilon)
    return max(-1.0, min(1.0, (buy - sell) / denominator))


def _shares(values: Iterable[float | int]) -> list[float]:
    clean = [float(value) for value in values if float(value) > 0]
    if any(not math.isfinite(value) for value in clean):
        raise ValueError("concentration inputs must be finite")
    total = sum(clean)
    if total <= 0:
        return []
    return [value / total for value in clean]


def herfindahl_hirschman_index(values: Iterable[float | int]) -> float | None:
    shares = _shares(values)
    if not shares:
        return None
    return sum(share**2 for share in shares)


def wallet_entropy(values: Iterable[float | int]) -> float | None:
    shares = _shares(values)
    if not shares:
        return None
    return -sum(share * math.log(share) for share in shares)


def gini_coefficient(values: Iterable[float | int]) -> float | None:
    clean = sorted(float(value) for value in values if float(value) >= 0)
    if any(not math.isfinite(value) for value in clean):
        raise ValueError("gini inputs must be finite")
    if not clean:
        return None
    total = sum(clean)
    if total <= 0:
        return None
    n = len(clean)
    weighted_sum = sum((index + 1) * value for index, value in enumerate(clean))
    return (2 * weighted_sum) / (n * total) - (n + 1) / n

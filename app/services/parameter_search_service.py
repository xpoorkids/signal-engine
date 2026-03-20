from __future__ import annotations

from dataclasses import asdict, dataclass
import itertools
import math
import random
from typing import Any, Callable, Iterable


SelectorFn = Callable[[dict[str, Any], dict[str, Any]], bool]


@dataclass(frozen=True)
class SearchMetrics:
    selected: int
    total: int
    expectancy: float
    win_rate: float
    precision: float
    false_positive_rate: float
    max_drawdown: float
    stability: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchResult:
    params: dict[str, Any]
    metrics: SearchMetrics
    rank_key: tuple[float, float, float, float, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "metrics": self.metrics.as_dict(),
            "rank_key": list(self.rank_key),
        }


def expand_parameter_space(parameter_space: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(parameter_space.keys())
    values = [list(parameter_space[key]) for key in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def sample_parameter_space(
    parameter_space: dict[str, list[Any]],
    *,
    sample_size: int,
    seed: int = 42,
) -> list[dict[str, Any]]:
    all_sets = expand_parameter_space(parameter_space)
    if sample_size >= len(all_sets):
        return all_sets
    rng = random.Random(seed)
    return rng.sample(all_sets, sample_size)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _sequence_max_drawdown(values: Iterable[float]) -> float:
    peak = 0.0
    equity = 0.0
    drawdown = 0.0
    for item in values:
        equity += float(item)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def score_selected_outcomes(selected_records: list[dict[str, Any]], *, total_records: int) -> SearchMetrics:
    selected = len(selected_records)
    if selected == 0:
        return SearchMetrics(
            selected=0,
            total=total_records,
            expectancy=0.0,
            win_rate=0.0,
            precision=0.0,
            false_positive_rate=0.0,
            max_drawdown=0.0,
            stability=0.0,
        )

    pnl_values = [_safe_float(item.get("pnl_pct"), _safe_float(item.get("pnl"), 0.0)) for item in selected_records]
    winners = sum(1 for item in pnl_values if item > 0)
    losers = sum(1 for item in pnl_values if item <= 0)
    expectancy = sum(pnl_values) / max(1, selected)
    win_rate = winners / max(1, selected)
    precision = win_rate
    false_positive_rate = losers / max(1, selected)
    max_drawdown = _sequence_max_drawdown(pnl_values)

    by_dataset: dict[str, list[float]] = {}
    for item, pnl in zip(selected_records, pnl_values):
        dataset = str(item.get("dataset") or "default")
        by_dataset.setdefault(dataset, []).append(pnl)
    grouped_expectancies = [sum(items) / max(1, len(items)) for items in by_dataset.values()]
    if len(grouped_expectancies) <= 1:
        stability = 1.0
    else:
        mean = sum(grouped_expectancies) / len(grouped_expectancies)
        variance = sum((value - mean) ** 2 for value in grouped_expectancies) / len(grouped_expectancies)
        stddev = math.sqrt(max(variance, 0.0))
        denom = max(abs(mean), 1.0)
        stability = max(0.0, 1.0 - min(1.0, stddev / denom))

    return SearchMetrics(
        selected=selected,
        total=total_records,
        expectancy=round(expectancy, 6),
        win_rate=round(win_rate, 6),
        precision=round(precision, 6),
        false_positive_rate=round(false_positive_rate, 6),
        max_drawdown=round(max_drawdown, 6),
        stability=round(stability, 6),
    )


def rank_search_results(results: list[SearchResult]) -> list[SearchResult]:
    return sorted(
        results,
        key=lambda item: item.rank_key,
        reverse=True,
    )


def run_parameter_search(
    *,
    records: list[dict[str, Any]],
    parameter_space: dict[str, list[Any]],
    selector: SelectorFn,
    mode: str = "grid",
    sample_size: int | None = None,
    seed: int = 42,
) -> list[SearchResult]:
    if mode not in {"grid", "random"}:
        raise ValueError("unsupported_search_mode")
    if mode == "grid":
        parameter_sets = expand_parameter_space(parameter_space)
    else:
        parameter_sets = sample_parameter_space(
            parameter_space,
            sample_size=max(1, int(sample_size or 1)),
            seed=seed,
        )

    results: list[SearchResult] = []
    for params in parameter_sets:
        selected_records = [record for record in records if selector(record, params)]
        metrics = score_selected_outcomes(selected_records, total_records=len(records))
        rank_key = (
            metrics.expectancy,
            metrics.precision,
            -metrics.max_drawdown,
            metrics.stability,
            metrics.selected,
        )
        results.append(SearchResult(params=dict(params), metrics=metrics, rank_key=rank_key))
    return rank_search_results(results)

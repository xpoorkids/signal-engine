from __future__ import annotations

from dataclasses import dataclass


CURRENT_JUPITER_QUOTE_GUARD = "current_jupiter_quote_cannot_be_used_as_historical_quote"


@dataclass(frozen=True)
class ExecutionEstimate:
    size_usd: float
    buy_impact_pct: float | None
    sell_impact_pct: float | None
    round_trip_cost_pct: float | None
    route_available: bool
    quality: str
    notes: list[str]


def reject_current_jupiter_for_historical_quote(snapshot_ts: int, quote_ts: int | None) -> None:
    if quote_ts is None:
        return
    if abs(int(quote_ts) - int(snapshot_ts)) > 300:
        raise ValueError(CURRENT_JUPITER_QUOTE_GUARD)


def reserve_execution_estimate(*, size_usd: float, liquidity_usd: float | None, quality: str = "historical_liquidity_estimated") -> ExecutionEstimate:
    if not liquidity_usd or liquidity_usd <= 0:
        return ExecutionEstimate(size_usd, None, None, None, False, "no_route", ["missing historical liquidity"])
    impact = min(99.0, (size_usd / liquidity_usd) * 100.0)
    return ExecutionEstimate(size_usd, impact, impact * 1.15, impact * 2.15 + 1.0, True, quality, ["AMM reserve estimate requires validation against nearby swaps"])


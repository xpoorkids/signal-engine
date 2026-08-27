from __future__ import annotations

import pytest

from research.execution import CURRENT_JUPITER_QUOTE_GUARD, reject_current_jupiter_for_historical_quote, reserve_execution_estimate


def test_current_jupiter_result_rejected_for_past_timestamp() -> None:
    with pytest.raises(ValueError, match=CURRENT_JUPITER_QUOTE_GUARD):
        reject_current_jupiter_for_historical_quote(1_700_000_000, 1_800_000_000)


def test_execution_impact_is_monotonic_by_size() -> None:
    small = reserve_execution_estimate(size_usd=100, liquidity_usd=10_000)
    large = reserve_execution_estimate(size_usd=500, liquidity_usd=10_000)
    assert small.route_available
    assert large.buy_impact_pct >= small.buy_impact_pct
    assert large.sell_impact_pct >= small.sell_impact_pct


def test_missing_route_is_labeled() -> None:
    estimate = reserve_execution_estimate(size_usd=100, liquidity_usd=None)
    assert estimate.quality == "no_route"
    assert estimate.route_available is False


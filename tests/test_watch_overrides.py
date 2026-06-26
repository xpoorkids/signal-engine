from __future__ import annotations

from worker import watch_overrides


def test_consumable_watch_override_requires_market_cap_and_liquidity(monkeypatch):
    monkeypatch.setattr(
        watch_overrides,
        "_get_override",
        lambda token: {
            "token": token,
            "override_id": "watch-123",
            "status": "active",
            "target_market_cap_usd": 100000.0,
            "min_liquidity_usd": 15000.0,
        },
    )

    assert (
        watch_overrides.resolve_consumable_watch_override(
            "token-override",
            market_cap_usd=99_000,
            liquidity_usd=20_000,
        )
        is None
    )
    assert (
        watch_overrides.resolve_consumable_watch_override(
            "token-override",
            market_cap_usd=120_000,
            liquidity_usd=14_000,
        )
        is None
    )

    result = watch_overrides.resolve_consumable_watch_override(
        "token-override",
        market_cap_usd=120_000,
        liquidity_usd=20_000,
    )

    assert result is not None
    assert result["consumable"] is True
    assert result["checks"]["market_cap_ok"] is True
    assert result["checks"]["liquidity_ok"] is True

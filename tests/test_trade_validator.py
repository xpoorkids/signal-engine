from worker.trade_validator import (
    build_pair_context,
    simulate_buy_quote,
    simulate_sell_quote,
    validate_trade,
)
from worker import route_quote, trade_validator


def _pair(liq_usd=50000.0, price_usd=0.5):
    return {
        "pairAddress": "pair-1",
        "dexId": "raydium",
        "priceUsd": str(price_usd),
        "liquidity": {"usd": liq_usd},
        "baseToken": {"address": "token-1", "symbol": "TOK"},
        "quoteToken": {"address": "So111", "symbol": "SOL"},
    }


def test_quote_simulation_round_trip_exists():
    ctx = build_pair_context(_pair(), "token-1")

    buy = simulate_buy_quote(ctx, 250.0)
    sell = simulate_sell_quote(ctx, buy.expected_output_tokens if buy else 0.0)

    assert ctx is not None
    assert buy is not None
    assert sell is not None
    assert buy.route_exists is True
    assert sell.route_exists is True
    assert buy.slippage_bps > 0
    assert sell.slippage_bps > 0


def test_validate_trade_approves_clean_setup():
    snapshot_ts = 2_000_000_000
    result = validate_trade(
        token="token-1",
        best_pair=_pair(),
        dex_summary={"liquidity_usd": 50000.0, "snapshot_ts": snapshot_ts},
        token_meta={"decimals": 6},
        risk_score=0.20,
        wallet_risk={"top_holder_pct": 0.04},
        mint_authority=False,
        freeze_authority=False,
        top_holder_ratio=0.10,
        intended_size_usd=100.0,
    )

    assert result["approved"] is True
    assert result["reasons"] == []
    assert result["quote_expires_ts"] > result["validated_ts"]
    assert result["market_data"]["snapshot_ts"] == snapshot_ts
    assert result["buy_quote"]["slippage_bps"] < 250
    assert result["sell_quote"]["slippage_bps"] < 350


def test_validate_trade_rejects_on_authority_and_sell_slippage():
    result = validate_trade(
        token="token-1",
        best_pair=_pair(liq_usd=6000.0, price_usd=0.25),
        dex_summary={"liquidity_usd": 6000.0},
        token_meta={"decimals": 6},
        risk_score=0.20,
        wallet_risk={"top_holder_pct": 0.20},
        mint_authority=True,
        freeze_authority=False,
        top_holder_ratio=0.25,
        intended_size_usd=1000.0,
    )

    reasons = set(result["reasons"])
    assert result["approved"] is False
    assert "mint_authority_active" in reasons
    assert "wallet_holder_concentration" in reasons
    assert "top_holder_concentration" in reasons
    assert "liquidity_below_threshold" in reasons or "buy_slippage_too_high" in reasons or "sell_slippage_too_high" in reasons


def test_validate_trade_rejects_price_pump_without_flow():
    result = validate_trade(
        token="token-1",
        best_pair=_pair(liq_usd=50000.0, price_usd=0.5),
        dex_summary={
            "liquidity_usd": 50000.0,
            "price_change_m5": 44.0,
            "price_change_h1": 150.0,
            "volume_m5": 14000.0,
            "txns_m5_buys": 8,
            "txns_m5_sells": 3,
            "snapshot_ts": 2_000_000_000,
        },
        token_meta={"decimals": 6},
        risk_score=0.20,
        wallet_risk={"top_holder_pct": 0.04},
        mint_authority=False,
        freeze_authority=False,
        top_holder_ratio=0.10,
        intended_size_usd=100.0,
    )

    assert result["approved"] is False
    assert "price_pump_without_flow" in result["reasons"]
    assert "one_sided_chart_risk" in result["reasons"]


def test_validate_trade_approves_high_flow_runner_shape():
    result = validate_trade(
        token="token-1",
        best_pair=_pair(liq_usd=120000.0, price_usd=0.5),
        dex_summary={
            "liquidity_usd": 120000.0,
            "price_change_m5": 28.0,
            "price_change_h1": 90.0,
            "volume_m5": 18000.0,
            "txns_m5_buys": 72,
            "txns_m5_sells": 22,
            "snapshot_ts": 2_000_000_000,
        },
        token_meta={"decimals": 6},
        risk_score=0.20,
        wallet_risk={"top_holder_pct": 0.04},
        mint_authority=False,
        freeze_authority=False,
        top_holder_ratio=0.10,
        intended_size_usd=100.0,
    )

    assert result["approved"] is True
    assert result["reasons"] == []


def test_validate_trade_rejects_stale_market_data(monkeypatch):
    monkeypatch.setattr("worker.trade_validator.time.time", lambda: 1000.0)
    result = validate_trade(
        token="token-1",
        best_pair=_pair(),
        dex_summary={"liquidity_usd": 50000.0, "snapshot_ts": 900},
        token_meta={"decimals": 6},
        risk_score=0.20,
        wallet_risk={"top_holder_pct": 0.04},
        mint_authority=False,
        freeze_authority=False,
        top_holder_ratio=0.10,
        intended_size_usd=100.0,
    )

    assert result["approved"] is False
    assert "market_data_stale" in result["reasons"]


def test_validate_trade_rejects_when_venue_quotes_required(monkeypatch):
    monkeypatch.setattr(trade_validator, "TRADE_VALIDATION_REQUIRE_VENUE_QUOTES", True)
    monkeypatch.setattr(route_quote, "TRADE_VALIDATION_REQUIRE_VENUE_QUOTES", True)
    monkeypatch.setattr(route_quote, "TRADE_VALIDATION_QUOTE_PROVIDER", "jupiter")
    monkeypatch.setattr(route_quote, "JUPITER_API_KEY", "")

    result = validate_trade(
        token="token-1",
        best_pair=_pair(),
        dex_summary={"liquidity_usd": 50000.0, "snapshot_ts": 2_000_000_000},
        token_meta={"decimals": 6},
        risk_score=0.20,
        wallet_risk={"top_holder_pct": 0.04},
        mint_authority=False,
        freeze_authority=False,
        top_holder_ratio=0.10,
        intended_size_usd=100.0,
    )

    assert result["approved"] is False
    assert "venue_quote_unavailable" in result["reasons"]

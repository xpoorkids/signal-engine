from worker.expected_value import evaluate_candidate_ev


def _trade_validation(**overrides):
    payload = {
        "approved": True,
        "intended_size_usd": 250.0,
        "reasons": [],
        "warnings": [],
        "market_data": {"liquidity_usd": 60000.0},
        "buy_quote": {
            "slippage_bps": 110.0,
            "price_impact_pct": 0.8,
            "route_labels": ["Raydium"],
        },
        "sell_quote": {
            "slippage_bps": 140.0,
            "price_impact_pct": 1.1,
            "route_labels": ["Raydium"],
        },
    }
    payload.update(overrides)
    return payload


def test_candidate_ev_approves_tradeable_positive_edge_setup():
    result = evaluate_candidate_ev(
        _trade_validation(),
        attention_score=0.72,
        risk_score=0.20,
        dex_summary={"liquidity_usd": 60000.0},
    )

    assert result["approved"] is True
    assert result["net_edge_bps"] > 0
    assert result["round_trip_slippage_bps"] == 250.0
    assert result["max_price_impact_pct"] == 1.1
    assert result["reasons"] == ["ev_gate_passed"]


def test_candidate_ev_rejects_failed_trade_validation():
    result = evaluate_candidate_ev(
        _trade_validation(approved=False, reasons=["wallet_holder_concentration"]),
        attention_score=0.92,
        risk_score=0.10,
        dex_summary={"liquidity_usd": 75000.0},
    )

    assert result["approved"] is False
    assert "trade_validation:wallet_holder_concentration" in result["reasons"]


def test_candidate_ev_rejects_bad_route_costs_and_liquidity():
    result = evaluate_candidate_ev(
        _trade_validation(
            market_data={"liquidity_usd": 12000.0},
            buy_quote={"slippage_bps": 420.0, "price_impact_pct": 4.2},
            sell_quote={"slippage_bps": 390.0, "price_impact_pct": 3.5},
        ),
        attention_score=0.55,
        risk_score=0.45,
        dex_summary={"liquidity_usd": 12000.0},
    )

    assert result["approved"] is False
    assert "liquidity_below_ev_floor" in result["reasons"]
    assert "round_trip_slippage_too_high" in result["reasons"]
    assert "price_impact_too_high" in result["reasons"]

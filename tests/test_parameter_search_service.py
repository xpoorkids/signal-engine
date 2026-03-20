from app.services.parameter_search_service import (
    build_signal_parameter_space,
    evaluate_signal_parameter_set,
    run_parameter_search,
    run_signal_parameter_sweep,
    score_selected_outcomes,
)


def _selector(record: dict, params: dict) -> bool:
    return (
        float(record.get("attention_score") or 0.0) >= float(params["min_attention"])
        and float(record.get("risk_score") or 1.0) <= float(params["max_risk"])
    )


def test_score_selected_outcomes_reports_expectancy_and_stability():
    metrics = score_selected_outcomes(
        [
            {"dataset": "a", "pnl_pct": 10.0},
            {"dataset": "a", "pnl_pct": -5.0},
            {"dataset": "b", "pnl_pct": 8.0},
        ],
        total_records=5,
    )

    assert metrics.selected == 3
    assert metrics.expectancy > 0
    assert metrics.win_rate > 0.6
    assert metrics.max_drawdown >= 0
    assert 0.0 <= metrics.stability <= 1.0


def test_run_parameter_search_ranks_higher_expectancy_lower_false_positive_sets_first():
    records = [
        {"dataset": "a", "attention_score": 0.70, "risk_score": 0.20, "pnl_pct": 15.0},
        {"dataset": "a", "attention_score": 0.68, "risk_score": 0.25, "pnl_pct": 9.0},
        {"dataset": "b", "attention_score": 0.40, "risk_score": 0.55, "pnl_pct": -8.0},
        {"dataset": "b", "attention_score": 0.50, "risk_score": 0.35, "pnl_pct": 2.0},
    ]
    results = run_parameter_search(
        records=records,
        parameter_space={
            "min_attention": [0.45, 0.65],
            "max_risk": [0.30, 0.60],
        },
        selector=_selector,
        mode="grid",
    )

    assert results
    assert results[0].metrics.false_positive_rate <= results[-1].metrics.false_positive_rate
    assert results[0].metrics.expectancy >= results[-1].metrics.expectancy


def test_evaluate_signal_parameter_set_classifies_fast_sniper_snapshot():
    decision = evaluate_signal_parameter_set(
        {
            "attention_score": 0.82,
            "confidence_score": 0.79,
            "risk_score": 0.18,
            "creator_score": 0.42,
            "elite_score": 10,
            "unique_10s": 3,
            "burst_10s": 9,
            "unique_buyers_5m": 7,
            "burst_count_60s": 11,
            "tracked_wallet_hits": 1,
            "kol_wallet_hits": 0,
            "x_tweet_count": 12,
            "x_unique_authors": 10,
            "liquidity_usd": 24000,
            "txns_m5_buys": 16,
            "txns_m5_sells": 4,
            "age_minutes": 2.5,
            "token_is_tradeable": True,
            "bonding_curve_verified": True,
        },
        {
            "score.min_confidence": 0.55,
        },
    )

    assert decision.predicted_route == "sniper"
    assert decision.heating_allowed is True
    assert decision.route_tier == "sniper"
    assert "tracked_wallet_flow" in decision.route_confirmations


def test_run_signal_parameter_sweep_prefers_more_robust_policy_mix():
    records = [
        {
            "dataset": "a",
            "target_route": "sniper",
            "attention_score": 0.84,
            "confidence_score": 0.82,
            "risk_score": 0.16,
            "creator_score": 0.41,
            "elite_score": 10,
            "unique_10s": 3,
            "burst_10s": 9,
            "unique_buyers_5m": 8,
            "burst_count_60s": 11,
            "tracked_wallet_hits": 1,
            "x_tweet_count": 11,
            "x_unique_authors": 10,
            "liquidity_usd": 22000,
            "txns_m5_buys": 15,
            "pnl_pct": 18.0,
        },
        {
            "dataset": "a",
            "target_route": "candidate",
            "attention_score": 0.61,
            "confidence_score": 0.64,
            "risk_score": 0.24,
            "creator_score": 0.67,
            "elite_score": 6,
            "unique_10s": 1,
            "burst_10s": 4,
            "unique_buyers_5m": 5,
            "burst_count_60s": 7,
            "tracked_wallet_hits": 0,
            "x_tweet_count": 6,
            "x_unique_authors": 4,
            "liquidity_usd": 14000,
            "txns_m5_buys": 10,
            "pnl_pct": 7.0,
        },
        {
            "dataset": "b",
            "target_route": "reject",
            "attention_score": 0.57,
            "confidence_score": 0.58,
            "risk_score": 0.45,
            "creator_score": 0.20,
            "elite_score": 5,
            "unique_10s": 1,
            "burst_10s": 6,
            "unique_buyers_5m": 2,
            "burst_count_60s": 5,
            "tracked_wallet_hits": 0,
            "x_tweet_count": 1,
            "x_unique_authors": 1,
            "liquidity_usd": 6000,
            "txns_m5_buys": 4,
            "pnl_pct": -9.0,
        },
        {
            "dataset": "b",
            "target_route": "heating_up",
            "attention_score": 0.64,
            "confidence_score": 0.66,
            "risk_score": 0.28,
            "creator_score": 0.33,
            "elite_score": 8,
            "unique_10s": 2,
            "burst_10s": 6,
            "unique_buyers_5m": 5,
            "burst_count_60s": 8,
            "tracked_wallet_hits": 0,
            "kol_wallet_hits": 1,
            "x_tweet_count": 10,
            "x_unique_authors": 10,
            "liquidity_usd": 17000,
            "txns_m5_buys": 11,
            "pnl_pct": 9.0,
        },
    ]
    results = run_signal_parameter_sweep(
        records=records,
        parameter_space={
            "route.sniper_min_confirmations": [2, 3],
            "candidate.min_send_confirmation_signals": [2, 3],
            "score.min_confidence": [0.55, 0.70],
        },
        mode="grid",
    )

    assert results
    assert results[0].metrics.robustness >= results[-1].metrics.robustness
    assert results[0].metrics.route_accuracy >= results[-1].metrics.route_accuracy
    assert results[0].route_counts["sniper"] >= 1


def test_build_signal_parameter_space_exposes_tunable_groups():
    parameter_space = build_signal_parameter_space()

    assert "candidate.min_confirmation_signals" in parameter_space
    assert "route.sniper_min_attention" in parameter_space
    assert "route.heating_delivery_min_confidence" in parameter_space
    assert "score.attention_weight" in parameter_space

from app.services.parameter_search_service import run_parameter_search, score_selected_outcomes


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

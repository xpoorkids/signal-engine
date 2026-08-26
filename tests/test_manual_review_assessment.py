from app.services.review_service import _manual_buy_assessment


def test_manual_buy_assessment_hard_fails_authority_risk():
    assessment = _manual_buy_assessment(
        attention_score=0.95,
        risk_score=0.10,
        elite_score=8,
        dex_summary={"liquidity_usd": 100000, "volume_m5": 50000, "txns_m5_buys": 50, "txns_m5_sells": 10},
        security={"mint_authority_active": True, "freeze_authority_active": False},
        rug_check={"verdict": "low"},
    )

    assert assessment["action"] == "HARD_FAIL"
    assert "mint_authority_active" in assessment["blockers"]
    assert assessment["calibration_status"] == "heuristic_uncalibrated"


def test_manual_buy_assessment_validated_watch_requires_flow_liquidity_and_low_risk():
    assessment = _manual_buy_assessment(
        attention_score=0.82,
        risk_score=0.20,
        elite_score=7,
        dex_summary={"liquidity_usd": 30000, "volume_m5": 20000, "txns_m5_buys": 20, "txns_m5_sells": 10},
        security={"mint_authority_active": False, "freeze_authority_active": False},
        rug_check={"verdict": "low"},
    )

    assert assessment["action"] == "VALIDATED_WATCH"
    assert "tradable_liquidity_observed" in assessment["positive_reasons"]
    assert "buy_flow_constructive" in assessment["positive_reasons"]
    assert assessment["not_financial_advice"] is True


def test_manual_buy_assessment_does_not_treat_attention_alone_as_buy():
    assessment = _manual_buy_assessment(
        attention_score=0.90,
        risk_score=0.20,
        elite_score=7,
        dex_summary={"liquidity_usd": 1000, "volume_m5": 500, "txns_m5_buys": 2, "txns_m5_sells": 1},
        security={"mint_authority_active": False, "freeze_authority_active": False},
        rug_check={"verdict": "low"},
    )

    assert assessment["action"] == "AVOID"
    assert "liquidity_too_thin" in assessment["blockers"]

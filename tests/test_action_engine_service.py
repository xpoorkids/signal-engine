from app.services.action_engine_service import ActionEngineService, calculate_tokens_to_recover_principal
from app.services.manual_position_service import ManualPositionService


def _engine(tmp_path):
    return ActionEngineService(tmp_path / "actions.db")


def _good_market(**overrides):
    data = {
        "liquidity_usd": 50000,
        "volume_m5": 25000,
        "txns_m5_buys": 30,
        "txns_m5_sells": 12,
        "price_change_m5": 8,
        "buy_route_ok": True,
        "sell_route_ok": True,
        "quote_fresh": True,
        "buy_impact_pct": 1,
        "sell_impact_pct": 1.5,
        "round_trip_cost_pct": 3,
        "organic_flow_windows": 2,
        "wallet_or_fee_confirmation": True,
        "maximum_safe_size_usd": 500,
    }
    data.update(overrides)
    return data


def _assessment(attention=0.9, risk=0.15):
    return {
        "attention_score": attention,
        "risk_score": risk,
        "manual_buy_assessment": {"action": "VALIDATED_WATCH", "summary": {"attention_score": attention, "risk_score": risk}},
        "security": {"mint_authority_active": False, "freeze_authority_active": False},
        "rug_check": {"verdict": "low"},
    }


def test_principal_recovery_exact_tokens_at_common_multiples():
    assert calculate_tokens_to_recover_principal(100, 1.5) == 66.66666666666667
    assert calculate_tokens_to_recover_principal(100, 2) == 50
    assert calculate_tokens_to_recover_principal(100, 3) == 33.333333333333336
    assert calculate_tokens_to_recover_principal(0, 3) == 0
    assert calculate_tokens_to_recover_principal(100, 0) is None


def test_buy_now_shadow_action_and_percentages(tmp_path):
    rec = _engine(tmp_path).recommend_for_token("token-a", market=_good_market(), assessment=_assessment(), intended_size_usd=250)

    assert rec["action"] == "BUY NOW"
    assert rec["display_action"] == "BUY NOW SHADOW"
    assert rec["calibration_status"] == "HEURISTIC_UNCALIBRATED"
    assert rec["probability_target_before_invalidation_pct"] >= 55
    assert rec["estimated_net_return_pct"] >= 6
    assert rec["recommended_initial_size_pct"] == 100
    assert rec["automation"]["executes_trades"] is False


def test_buy_small_wait_wait_pullback_do_not_chase_avoid_and_hard_fail(tmp_path):
    engine = _engine(tmp_path)

    buy_small = engine.recommend_for_token(
        "token-small",
        market=_good_market(volume_m5=6000, txns_m5_buys=6, txns_m5_sells=5, organic_flow_windows=1, wallet_or_fee_confirmation=False),
        assessment=_assessment(attention=0.55, risk=0.22),
    )
    assert buy_small["action"] == "BUY SMALL"
    assert buy_small["recommended_initial_size_pct"] == 40

    wait = engine.recommend_for_token("token-wait", market=_good_market(quote_fresh=False), assessment=_assessment())
    assert wait["action"] in {"HARD FAIL", "WAIT"}
    assert "stale_execution_data" in wait["blockers"]

    pullback = engine.recommend_for_token(
        "token-pullback",
        market=_good_market(price_extension_from_preferred_entry_pct=28, estimated_net_return_pct=4),
        assessment=_assessment(attention=0.72, risk=0.2),
    )
    assert pullback["action"] in {"WAIT FOR PULLBACK", "DO NOT CHASE"}

    chase = engine.recommend_for_token(
        "token-chase",
        market=_good_market(price_extension_from_preferred_entry_pct=45, txns_m5_buys=4, txns_m5_sells=5, organic_flow_windows=0, wallet_or_fee_confirmation=False),
        assessment=_assessment(attention=0.55, risk=0.25),
    )
    assert chase["action"] == "DO NOT CHASE"

    avoid = engine.recommend_for_token("token-avoid", market=_good_market(liquidity_usd=3000, sell_route_ok=True), assessment=_assessment(attention=0.2, risk=0.7))
    assert avoid["action"] == "AVOID"

    hard = engine.recommend_for_token("token-hard", market=_good_market(), assessment={"security": {"mint_authority_active": True}, "rug_check": {"verdict": "high"}})
    assert hard["action"] == "HARD FAIL"
    assert "dangerous_token_authority" in hard["blockers"]


def test_missing_sell_route_and_excessive_impact_are_hard_safety(tmp_path):
    engine = _engine(tmp_path)

    missing = engine.recommend_for_token("token-route", market=_good_market(sell_route_ok=False), assessment=_assessment())
    impact = engine.recommend_for_token("token-impact", market=_good_market(sell_impact_pct=12), assessment=_assessment())

    assert missing["action"] == "HARD FAIL"
    assert "no_sell_route" in missing["blockers"]
    assert impact["action"] == "HARD FAIL"
    assert "impossible_price_impact" in impact["blockers"]


def test_catalyst_buy_now_buy_small_priced_in_and_hard_safety(tmp_path):
    engine = _engine(tmp_path)
    catalyst = {"verification_status": "active", "catalyst_confidence_pct": 88, "catalyst_flow_confirmation": True}

    buy_now = engine.recommend_for_token("token-cat", market=_good_market(), assessment=_assessment(), catalyst=catalyst)
    assert buy_now["action"] == "CATALYST BUY NOW"
    assert buy_now["display_action"] == "CATALYST BUY NOW SHADOW"

    buy_small = engine.recommend_for_token(
        "token-cat-small",
        market=_good_market(volume_m5=6000, txns_m5_buys=6, txns_m5_sells=5, organic_flow_windows=1, wallet_or_fee_confirmation=False),
        assessment=_assessment(attention=0.52, risk=0.25),
        catalyst={"verification_status": "verified", "catalyst_confidence_pct": 70, "catalyst_flow_confirmation": False},
    )
    assert buy_small["action"] == "CATALYST BUY SMALL"

    priced = engine.recommend_for_token(
        "token-priced",
        market=_good_market(price_extension_from_preferred_entry_pct=45, txns_m5_buys=5, txns_m5_sells=5),
        assessment=_assessment(attention=0.65, risk=0.2),
        catalyst={"verification_status": "priced_in", "catalyst_confidence_pct": 80, "catalyst_flow_confirmation": False},
    )
    assert priced["action"] == "DO NOT CHASE"
    assert "CATALYST PRICED IN" in priced["warnings"]

    hard = engine.recommend_for_token("token-cat-hard", market=_good_market(sell_route_ok=False), assessment=_assessment(), catalyst=catalyst)
    assert hard["action"] == "HARD FAIL"


def test_runner_target_varies_by_catalyst_state(tmp_path):
    engine = _engine(tmp_path)

    assert engine.runner_target_pct(None) == 25
    assert engine.runner_target_pct({"verification_status": "verified"}) == 35
    assert engine.runner_target_pct({"verification_status": "flow_confirmed", "catalyst_flow_confirmation": True}) == 50
    assert engine.runner_target_pct({"verification_status": "high_conviction", "catalyst_flow_confirmation": True}) == 60
    assert engine.runner_target_pct({"verification_status": "invalidated", "catalyst_flow_confirmation": True}) == 25


def test_position_recommendations_preserve_runner_and_recover_principal(tmp_path):
    positions = ManualPositionService(tmp_path / "actions.db")
    position = positions.mark_bought(token="token-pos", token_quantity=100, gross_usd=100)
    engine = ActionEngineService(tmp_path / "actions.db", positions=positions)

    rec = engine.recommend_for_position(
        position["position_id"],
        market={"current_executable_value_usd": 220, "executable_net_sell_value_per_token": 2.2, "sell_route_ok": True, "sell_impact_pct": 2},
    )

    assert rec["action"] == "RECOVER PRINCIPAL"
    assert round(rec["tokens_to_recover_principal"], 4) == 45.4545
    assert rec["recommended_sell_tokens"] == rec["tokens_to_recover_principal"]
    assert rec["recommended_sell_pct"] < 100


def test_profit_target_does_not_sell_below_runner_floor(tmp_path):
    positions = ManualPositionService(tmp_path / "actions.db")
    position = positions.mark_bought(token="token-run", token_quantity=100, gross_usd=100)
    positions.record_sell(position["position_id"], token_quantity=60, gross_usd=100)
    engine = ActionEngineService(tmp_path / "actions.db", positions=positions)

    rec = engine.recommend_for_position(
        position["position_id"],
        market={"current_executable_value_usd": 80, "executable_net_sell_value_per_token": 2, "sell_route_ok": True, "sell_impact_pct": 2},
        catalyst={"verification_status": "flow_confirmed", "catalyst_flow_confirmation": True, "catalyst_confidence_pct": 85},
    )

    assert rec["action"] in {"HOLD MOON BAG", "HOLD", "RECOVER PRINCIPAL"}
    assert rec["recommended_sell_pct"] == 0 or rec["remaining_tokens"] >= 50


def test_hard_safety_overrides_runner_floor(tmp_path):
    positions = ManualPositionService(tmp_path / "actions.db")
    position = positions.mark_bought(token="token-emergency", token_quantity=100, gross_usd=100)
    engine = ActionEngineService(tmp_path / "actions.db", positions=positions)

    rec = engine.recommend_for_position(
        position["position_id"],
        market={
            "current_executable_value_usd": 120,
            "executable_net_sell_value_per_token": 1.2,
            "sell_route_ok": True,
            "sell_impact_pct": 13,
            "liquidity_change_2m_pct": -31,
        },
        catalyst={"verification_status": "high_conviction", "catalyst_flow_confirmation": True},
    )

    assert rec["action"] == "EMERGENCY EXIT"
    assert rec["recommended_sell_pct"] == 100


def test_add_small_requires_new_confirmation_and_blocks_averaging_down(tmp_path):
    positions = ManualPositionService(tmp_path / "actions.db")
    position = positions.mark_bought(token="token-add", token_quantity=100, gross_usd=100)
    engine = ActionEngineService(tmp_path / "actions.db", positions=positions)

    down_only = engine.recommend_for_position(
        position["position_id"],
        market={"current_executable_value_usd": 80, "executable_net_sell_value_per_token": 0.8, "sell_route_ok": True, "sell_impact_pct": 2},
    )
    confirmed = engine.recommend_for_position(
        position["position_id"],
        market={"current_executable_value_usd": 82, "executable_net_sell_value_per_token": 0.82, "sell_route_ok": True, "sell_impact_pct": 2, "positive_new_confirmation": True},
    )
    blocked = engine.recommend_for_position(
        position["position_id"],
        market={"current_executable_value_usd": 82, "executable_net_sell_value_per_token": 0.82, "sell_route_ok": True, "sell_impact_pct": 2, "positive_new_confirmation": True, "liquidity_deteriorating": True},
    )

    assert down_only["action"] != "ADD SMALL ON CONFIRMATION"
    assert confirmed["action"] == "ADD SMALL ON CONFIRMATION"
    assert blocked["action"] != "ADD SMALL ON CONFIRMATION"


def test_catalyst_weakening_invalidated_and_sell_now(tmp_path):
    positions = ManualPositionService(tmp_path / "actions.db")
    position = positions.mark_bought(token="token-cat-exit", token_quantity=100, gross_usd=100)
    engine = ActionEngineService(tmp_path / "actions.db", positions=positions)

    weakening = engine.recommend_for_position(position["position_id"], market={"current_executable_value_usd": 120, "sell_route_ok": True}, catalyst={"verification_status": "weakening"})
    invalidated = engine.recommend_for_position(position["position_id"], market={"current_executable_value_usd": 120, "sell_route_ok": True, "flow_reversing": True, "probability_continued_upside_pct": 30}, catalyst={"verification_status": "invalidated"})

    assert weakening["action"] == "CATALYST WEAKENING"
    assert invalidated["action"] == "SELL NOW"
    assert invalidated["recommended_sell_pct"] == 100


def test_recommendation_is_persisted_for_shadow_learning(tmp_path):
    engine = _engine(tmp_path)
    rec = engine.recommend_for_token("token-learn", market=_good_market(), assessment=_assessment())

    assert engine.record_recommendation_outcome(
        rec["recommendation_id"],
        {"return_1m_pct": 2, "return_5m_pct": 5, "target_reached_before_invalidation": True},
    )
    with engine._connect() as conn:
        row = conn.execute("SELECT * FROM action_recommendations WHERE recommendation_id=?", (rec["recommendation_id"],)).fetchone()
    assert row["action"] == rec["action"]
    assert row["outcome_json"] is not None

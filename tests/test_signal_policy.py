from worker.signal_policy import (
    adversarial_signal_flags,
    candidate_confirmation_signals,
    candidate_send_reasons,
    classify_route_signal,
    entry_quality_profile,
    heating_delivery_decision,
    promotion_confirmation_target,
)


def test_candidate_confirmation_signals_require_multi_factor_support():
    reasons, confirmations = candidate_confirmation_signals(
        attention_score=0.44,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 2,
                "burst_count_60s": 3,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={"liquidity_usd": 5000.0, "txns_m5_buys": 4},
    )

    assert "confirmation_signals<2" in reasons
    assert confirmations == []


def test_candidate_send_reasons_reject_concentrated_wallet_flow_without_support():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.62,
        creator_score=0.70,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 4,
                "burst_count_60s": 8,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
                "unique_wallets_30s": 2,
                "top_wallet_share_30s": 0.80,
            }
        },
        dex_summary={"liquidity_usd": 15000.0, "txns_m5_buys": 12},
    )

    assert eligible is False
    assert "concentrated_wallet_flow" in reasons
    assert "buyer_breadth" in confirmations


def test_candidate_send_reasons_reject_low_quality_attention_only_setup():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.60,
        creator_score=0.70,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 3,
                "burst_count_60s": 6,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
                "unique_wallets_30s": 4,
                "top_wallet_share_30s": 0.20,
            }
        },
        dex_summary={"liquidity_usd": 6000.0, "txns_m5_buys": 6},
    )

    assert eligible is False
    assert "quality_confirmation_missing" in reasons
    assert "strong_attention" in confirmations


def test_candidate_send_reasons_allow_fast_lane_heating_setup_with_flow_strength():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.74,
        creator_score=0.40,
        extra={
            "route_decision": {
                "tier": "heating_up",
                "route_confidence": 0.81,
            },
            "attention_metrics": {
                "unique_buyers_5m": 5,
                "burst_count_60s": 8,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
                "unique_wallets_30s": 5,
                "top_wallet_share_30s": 0.22,
            }
        },
        dex_summary={"liquidity_usd": 10000.0, "txns_m5_buys": 7},
    )

    assert eligible is True
    assert reasons == []
    assert "buyer_breadth" in confirmations
    assert "burst_strength" in confirmations


def test_candidate_send_reasons_allow_strong_dex_breakout_without_social_attention():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.20,
        creator_score=0.0,
        extra={"attention_metrics": {}},
        dex_summary={
            "liquidity_usd": 90_000.0,
            "volume_m5": 31_000.0,
            "txns_m5_buys": 180,
            "txns_m5_sells": 50,
            "price_change_m5": 12.0,
            "market_cap": 1_250_000.0,
        },
    )

    assert eligible is True
    assert reasons == []
    assert "market_support" in confirmations
    assert "dex_momentum" in confirmations
    assert "entry_buy_pressure" in confirmations


def test_candidate_send_reasons_accepts_live_market_cap_usd_for_dex_breakout():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.20,
        creator_score=0.0,
        extra={"attention_metrics": {}},
        dex_summary={
            "liquidity_usd": 36_000.0,
            "volume_m5": 14_000.0,
            "txns_m5_buys": 765,
            "txns_m5_sells": 79,
            "price_change_m5": 7.0,
            "market_cap_usd": 185_000.0,
        },
    )

    assert eligible is True
    assert reasons == []
    assert "market_support" in confirmations
    assert "dex_momentum" in confirmations
    assert "entry_buy_pressure" in confirmations


def test_paid_visibility_allows_confirmed_dex_flow_without_local_wallet_counters():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.20,
        creator_score=0.0,
        extra={
            "attention_metrics": {
                "paid_visibility": True,
                "unique_buyers_5m": 0,
                "burst_count_60s": 0,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={
            "liquidity_usd": 36_000.0,
            "volume_m5": 14_000.0,
            "txns_m5_buys": 765,
            "txns_m5_sells": 79,
            "price_change_m5": 7.0,
            "market_cap_usd": 185_000.0,
        },
    )

    assert eligible is True
    assert reasons == []
    assert "paid_visibility_without_flow" not in reasons
    assert "dex_momentum" in confirmations


def test_candidate_send_reasons_accepts_scanner_metric_aliases():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.20,
        creator_score=0.0,
        extra={
            "attention_metrics": {
                "dexscreener_boosts_count": 2,
                "unique_buyers_5m": 0,
                "burst_count_60s": 0,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={
            "liquidity_usd": 36_000.0,
            "volume_m5_usd": 21_000.0,
            "buys_5m": 958,
            "sells_5m": 174,
            "price_change_5m": 7.0,
            "market_cap_usd": 186_000.0,
        },
    )

    assert eligible is True
    assert reasons == []
    assert "paid_visibility_without_flow" not in reasons
    assert "market_support" in confirmations
    assert "dex_momentum" in confirmations
    assert "entry_buy_pressure" in confirmations


def test_adversarial_signal_flags_detect_bursty_shallow_liquidity_pattern():
    flags = adversarial_signal_flags(
        metrics={
            "unique_buyers_5m": 2,
            "burst_count_60s": 9,
            "tracked_wallet_hits": 0,
            "kol_wallet_hits": 0,
            "top_wallet_share_30s": 0.78,
            "unique_wallets_30s": 2,
        },
        dex_summary={
            "liquidity_usd": 7000.0,
            "volume_m5": 35000.0,
            "txns_m5_buys": 7,
            "txns_m5_sells": 11,
        },
        anti_wash_top_wallet_share=0.70,
        anti_wash_unique_wallets_30s=2,
        min_unique_buyers_5m=3,
        min_burst_count_60s=6,
        max_sell_ratio_5m=1.2,
        max_vol_liq_ratio_5m=4.0,
        shallow_liq_usd=12000.0,
    )

    assert "burst_without_breadth" in flags
    assert "concentrated_wallet_flow" in flags
    assert "shallow_liquidity_hype" in flags
    assert "volume_liquidity_imbalance" in flags
    assert "sell_pressure_elevated" in flags


def test_adversarial_signal_flags_detect_supply_control_from_holder_and_volume():
    flags = adversarial_signal_flags(
        metrics={
            "unique_buyers_5m": 5,
            "burst_count_60s": 8,
            "tracked_wallet_hits": 0,
            "kol_wallet_hits": 0,
            "top_holder_ratio": 0.052,
        },
        dex_summary={
            "liquidity_usd": 26000.0,
            "volume_h24": 70000.0,
            "market_cap_usd": 100000.0,
            "txns_m5_buys": 12,
            "txns_m5_sells": 5,
        },
        anti_wash_top_wallet_share=0.70,
        anti_wash_unique_wallets_30s=2,
        min_unique_buyers_5m=3,
        min_burst_count_60s=6,
        max_sell_ratio_5m=1.2,
        max_vol_liq_ratio_5m=4.0,
        shallow_liq_usd=12000.0,
        max_single_holder_ratio=0.035,
        min_volume_market_cap_ratio=0.80,
    )

    assert "single_holder_supply_control" in flags
    assert "low_volume_market_cap_imbalance" in flags


def test_candidate_send_reasons_reject_supply_control_setup_without_hard_quality():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.76,
        creator_score=0.50,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 5,
                "burst_count_60s": 8,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
                "top_holder_ratio": 0.041,
            }
        },
        dex_summary={
            "liquidity_usd": 26000.0,
            "volume_h24": 79000.0,
            "market_cap_usd": 100000.0,
            "txns_m5_buys": 12,
            "txns_m5_sells": 5,
        },
    )

    assert eligible is False
    assert "buyer_breadth" in confirmations
    assert "burst_strength" in confirmations
    assert "single_holder_supply_control" in reasons
    assert "low_volume_market_cap_imbalance" in reasons


def test_candidate_send_reasons_reject_supply_control_even_with_kol_flow():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.76,
        creator_score=0.60,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 5,
                "burst_count_60s": 8,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 1,
                "top_holder_ratio": 0.052,
            }
        },
        dex_summary={
            "liquidity_usd": 26000.0,
            "volume_h24": 70000.0,
            "market_cap_usd": 100000.0,
            "txns_m5_buys": 12,
            "txns_m5_sells": 5,
        },
    )

    assert eligible is False
    assert "kol_wallet_flow" in confirmations
    assert "single_holder_supply_control" in reasons
    assert "low_volume_market_cap_imbalance" in reasons


def test_candidate_send_reasons_reject_paid_visibility_without_flow():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.62,
        creator_score=0.70,
        extra={
            "attention_metrics": {
                "dexscreener_boosts_count": 2,
                "unique_buyers_5m": 1,
                "burst_count_60s": 2,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={"liquidity_usd": 22000.0, "txns_m5_buys": 10, "txns_m5_sells": 4},
    )

    assert eligible is False
    assert "market_support" in confirmations
    assert "paid_visibility_without_flow" in reasons


def test_candidate_send_reasons_reject_social_echo_chamber_without_trusted_flow():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.72,
        creator_score=0.55,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 4,
                "burst_count_60s": 8,
                "x_tweet_count": 12,
                "x_unique_authors": 2,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={"liquidity_usd": 18000.0, "txns_m5_buys": 10, "txns_m5_sells": 4},
    )

    assert eligible is False
    assert "social_support" not in confirmations
    assert "social_echo_chamber" in reasons


def test_candidate_confirmation_signals_include_heavy_x_support():
    reasons, confirmations = candidate_confirmation_signals(
        attention_score=0.40,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 2,
                "burst_count_60s": 3,
                "x_tweet_count": 2,
                "x_unique_authors": 2,
                "x_heavy_author_count": 1,
                "x_verified_author_count": 2,
                "x_author_followers": 75000,
            }
        },
        dex_summary={"liquidity_usd": 8000.0, "txns_m5_buys": 4},
    )

    assert "heavy_x_support" in confirmations
    assert "credible_x_reach" in confirmations
    assert "confirmation_signals<2" not in reasons


def test_heavy_x_support_is_not_social_echo_chamber():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.62,
        creator_score=0.60,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 4,
                "burst_count_60s": 8,
                "x_tweet_count": 12,
                "x_unique_authors": 2,
                "x_heavy_author_count": 1,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={"liquidity_usd": 18000.0, "txns_m5_buys": 10, "txns_m5_sells": 4},
    )

    assert eligible is True
    assert "heavy_x_support" in confirmations
    assert "social_echo_chamber" not in reasons


def test_community_takeover_counts_as_quality_support():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.58,
        creator_score=0.55,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 4,
                "burst_count_60s": 8,
                "community_takeover": True,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={"liquidity_usd": 26000.0, "txns_m5_buys": 12, "txns_m5_sells": 4},
    )

    assert eligible is True
    assert "community_takeover" in confirmations
    assert reasons == []


def test_paid_visibility_without_flow_is_blocked():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.62,
        creator_score=0.70,
        extra={
            "attention_metrics": {
                "paid_visibility": True,
                "unique_buyers_5m": 1,
                "burst_count_60s": 2,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={"liquidity_usd": 22000.0, "txns_m5_buys": 10, "txns_m5_sells": 4},
    )

    assert eligible is False
    assert "market_support" in confirmations
    assert "paid_visibility_without_flow" in reasons


def test_entry_quality_profile_marks_extended_chase_without_breadth():
    profile = entry_quality_profile(
        metrics={
            "unique_buyers_5m": 2,
            "tracked_wallet_hits": 0,
            "kol_wallet_hits": 0,
        },
        dex_summary={
            "liquidity_usd": 9000.0,
            "price_change_m5": 52.0,
            "price_change_h1": 180.0,
            "txns_m5_buys": 8,
            "txns_m5_sells": 9,
        },
    )

    assert profile["tier"] == "chase_risk"
    assert "entry_extended_without_breadth" in profile["reasons"]
    assert "entry_extended_thin_liquidity" in profile["reasons"]


def test_candidate_send_reasons_reject_chase_entry_without_trusted_flow():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.78,
        creator_score=0.70,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 2,
                "burst_count_60s": 8,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
            }
        },
        dex_summary={
            "liquidity_usd": 9000.0,
            "price_change_m5": 52.0,
            "price_change_h1": 180.0,
            "txns_m5_buys": 8,
            "txns_m5_sells": 9,
        },
    )

    assert eligible is False
    assert "entry_extended_without_breadth" in reasons
    assert "entry_extended_thin_liquidity" in reasons
    assert "burst_strength" in confirmations


def test_candidate_send_reasons_reject_adversarial_burst_without_hard_quality():
    eligible, reasons, _confirmations = candidate_send_reasons(
        attention_score=0.78,
        creator_score=0.42,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 2,
                "burst_count_60s": 9,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 0,
                "unique_wallets_30s": 2,
                "top_wallet_share_30s": 0.78,
            }
        },
        dex_summary={
            "liquidity_usd": 7000.0,
            "volume_m5": 35000.0,
            "txns_m5_buys": 7,
            "txns_m5_sells": 11,
        },
    )

    assert eligible is False
    assert "burst_without_breadth" in reasons
    assert "shallow_liquidity_hype" in reasons


def test_candidate_send_reasons_allow_smart_money_supported_early_runner():
    eligible, reasons, confirmations = candidate_send_reasons(
        attention_score=0.78,
        creator_score=0.42,
        extra={
            "route_decision": {"tier": "sniper", "route_confidence": 0.84},
            "attention_metrics": {
                "unique_buyers_5m": 2,
                "burst_count_60s": 9,
                "tracked_wallet_hits": 1,
                "kol_wallet_hits": 0,
                "unique_wallets_30s": 2,
                "top_wallet_share_30s": 0.78,
            }
        },
        dex_summary={
            "liquidity_usd": 7000.0,
            "volume_m5": 35000.0,
            "txns_m5_buys": 7,
            "txns_m5_sells": 11,
        },
    )

    assert eligible is True
    assert reasons == []
    assert "tracked_wallet_flow" in confirmations


def test_promotion_confirmation_target_scales_with_signal_strength():
    strong_target, strong_reasons = promotion_confirmation_target(
        confidence_score=0.90,
        confidence_min=0.80,
        attention_score=0.72,
        attention_min=0.50,
        risk_score=0.20,
        risk_max=0.60,
        liquidity_usd=40000.0,
        liquidity_min=15000.0,
        buyers_15m=45,
        buyers_15m_min=30,
        extra={"attention_metrics": {"tracked_wallet_hits": 1}},
        dex_summary={"txns_m5_buys": 20, "txns_m5_sells": 10},
    )
    weak_target, weak_reasons = promotion_confirmation_target(
        confidence_score=0.81,
        confidence_min=0.80,
        attention_score=0.51,
        attention_min=0.50,
        risk_score=0.55,
        risk_max=0.60,
        liquidity_usd=16000.0,
        liquidity_min=15000.0,
        buyers_15m=30,
        buyers_15m_min=30,
        extra={"attention_metrics": {"tracked_wallet_hits": 0}},
        dex_summary={"txns_m5_buys": 8, "txns_m5_sells": 12},
    )

    assert strong_target == 1
    assert "smart_money_support" in strong_reasons
    assert weak_target >= 3
    assert "sell_pressure_high" in weak_reasons


def test_promotion_confirmation_target_penalizes_adversarial_market_shape():
    target, reasons = promotion_confirmation_target(
        confidence_score=0.86,
        confidence_min=0.80,
        attention_score=0.64,
        attention_min=0.50,
        risk_score=0.32,
        risk_max=0.60,
        liquidity_usd=14000.0,
        liquidity_min=12000.0,
        buyers_15m=34,
        buyers_15m_min=30,
        extra={"attention_metrics": {"unique_buyers_5m": 2, "burst_count_60s": 9, "tracked_wallet_hits": 0, "kol_wallet_hits": 0}},
        dex_summary={"liquidity_usd": 7000.0, "volume_m5": 35000.0, "txns_m5_buys": 7, "txns_m5_sells": 11},
    )

    assert target >= 3
    assert "volume_liquidity_imbalance" in reasons


def test_promotion_confirmation_target_penalizes_kol_supported_supply_control():
    target, reasons = promotion_confirmation_target(
        confidence_score=0.88,
        confidence_min=0.80,
        attention_score=0.70,
        attention_min=0.50,
        risk_score=0.35,
        risk_max=0.60,
        liquidity_usd=26000.0,
        liquidity_min=12000.0,
        buyers_15m=40,
        buyers_15m_min=30,
        extra={
            "attention_metrics": {
                "unique_buyers_5m": 5,
                "burst_count_60s": 8,
                "tracked_wallet_hits": 0,
                "kol_wallet_hits": 1,
                "top_holder_ratio": 0.052,
            }
        },
        dex_summary={
            "liquidity_usd": 26000.0,
            "volume_h24": 70000.0,
            "market_cap_usd": 100000.0,
            "txns_m5_buys": 12,
            "txns_m5_sells": 5,
        },
    )

    assert target >= 2
    assert "single_holder_supply_control" in reasons
    assert "low_volume_market_cap_imbalance" in reasons


def test_classify_route_signal_distinguishes_sniper_from_watch():
    sniper = classify_route_signal(
        attention_score=0.70,
        elite_score=9,
        unique_10s=3,
        burst_10s=8,
        hard_fail_from_authority_checks=False,
        extra={"attention_metrics": {"tracked_wallet_hits": 1, "unique_buyers_5m": 5, "burst_count_60s": 9}},
        dex_summary={"liquidity_usd": 25000.0, "txns_m5_buys": 14},
    )
    watch = classify_route_signal(
        attention_score=0.25,
        elite_score=4,
        unique_10s=1,
        burst_10s=2,
        hard_fail_from_authority_checks=False,
        extra={"attention_metrics": {"tracked_wallet_hits": 0}},
        dex_summary={"liquidity_usd": 4000.0, "txns_m5_buys": 2},
    )

    assert sniper["tier"] == "sniper"
    assert "tracked_wallet_flow" in sniper["confirmations"]
    assert sniper["sniper_ready"] is True
    assert sniper["age_bypass_eligible"] is True
    assert sniper["age_bypass_ttl_sec"] > 0
    assert sniper["route_confidence"] >= 0.70
    assert watch["tier"] == "watch"
    assert watch["blockers"]


def test_classify_route_signal_keeps_strong_non_sniper_setup_as_heating_up():
    route = classify_route_signal(
        attention_score=0.58,
        elite_score=7,
        unique_10s=2,
        burst_10s=6,
        hard_fail_from_authority_checks=False,
        extra={"attention_metrics": {"tracked_wallet_hits": 0, "unique_buyers_5m": 4, "burst_count_60s": 8}},
        dex_summary={"liquidity_usd": 22000.0, "txns_m5_buys": 12},
    )

    assert route["tier"] == "heating_up"
    assert "market_support" in route["confirmations"]


def test_classify_route_signal_marks_near_sniper_for_short_age_bypass():
    route = classify_route_signal(
        attention_score=0.61,
        elite_score=8,
        unique_10s=3,
        burst_10s=7,
        hard_fail_from_authority_checks=False,
        extra={"attention_metrics": {"tracked_wallet_hits": 0, "x_tweet_count": 14, "x_unique_authors": 12}},
        dex_summary={"liquidity_usd": 22000.0, "txns_m5_buys": 12},
    )

    assert route["tier"] == "heating_up"
    assert route["sniper_near_miss"] is True
    assert "sniper_flow_confirmation_missing" in route["blockers"]
    assert route["age_bypass_eligible"] is True
    assert route["age_bypass_reason"] == "near_sniper_route"
    assert route["age_bypass_ttl_sec"] > 0


def test_classify_route_signal_blocks_social_only_near_sniper_age_bypass():
    route = classify_route_signal(
        attention_score=0.63,
        elite_score=8,
        unique_10s=3,
        burst_10s=7,
        hard_fail_from_authority_checks=False,
        extra={"attention_metrics": {"x_tweet_count": 14, "x_unique_authors": 12}},
        dex_summary={"liquidity_usd": 10000.0, "txns_m5_buys": 4},
    )

    assert route["tier"] == "watch"
    assert route["age_bypass_eligible"] is False
    assert "route_flow_confirmation_missing" in route["blockers"]


def test_classify_route_signal_keeps_heating_up_when_flow_is_strong_without_hard_quality():
    route = classify_route_signal(
        attention_score=0.66,
        elite_score=8,
        unique_10s=3,
        burst_10s=7,
        hard_fail_from_authority_checks=False,
        extra={"attention_metrics": {"unique_buyers_5m": 5, "burst_count_60s": 9}},
        dex_summary={"liquidity_usd": 9000.0, "txns_m5_buys": 7},
    )

    assert route["tier"] == "heating_up"
    assert route["age_bypass_eligible"] is False


def test_classify_route_signal_blocks_adversarial_fake_momentum_without_hard_quality():
    route = classify_route_signal(
        attention_score=0.78,
        elite_score=9,
        unique_10s=3,
        burst_10s=8,
        hard_fail_from_authority_checks=False,
        extra={"attention_metrics": {"unique_buyers_5m": 2, "burst_count_60s": 9, "tracked_wallet_hits": 0, "kol_wallet_hits": 0, "unique_wallets_30s": 2, "top_wallet_share_30s": 0.78}},
        dex_summary={"liquidity_usd": 7000.0, "volume_m5": 35000.0, "txns_m5_buys": 7, "txns_m5_sells": 11},
    )

    assert route["tier"] == "watch"
    assert "shallow_liquidity_hype" in route["blockers"]
    assert "volume_liquidity_imbalance" in route["blockers"]


def test_classify_route_signal_keeps_smart_money_supported_runner_as_sniper():
    route = classify_route_signal(
        attention_score=0.78,
        elite_score=9,
        unique_10s=3,
        burst_10s=8,
        hard_fail_from_authority_checks=False,
        extra={"attention_metrics": {"unique_buyers_5m": 2, "burst_count_60s": 9, "tracked_wallet_hits": 1, "kol_wallet_hits": 0, "unique_wallets_30s": 2, "top_wallet_share_30s": 0.78}},
        dex_summary={"liquidity_usd": 7000.0, "volume_m5": 35000.0, "txns_m5_buys": 7, "txns_m5_sells": 11},
    )

    assert route["tier"] == "sniper"
    assert route["sniper_ready"] is True


def test_heating_delivery_decision_trusts_sniper_route():
    allowed, reasons = heating_delivery_decision(
        {
            "route_decision": {
                "tier": "sniper",
                "confirmations": ["tracked_wallet_flow", "market_support"],
                "blockers": [],
                "route_confidence": 0.91,
            }
        }
    )

    assert allowed is True
    assert "sniper_route" in reasons


def test_heating_delivery_decision_blocks_weak_market_only_heating_alert():
    allowed, reasons = heating_delivery_decision(
        {
            "route_decision": {
                "tier": "heating_up",
                "confirmations": ["market_support", "attention_support"],
                "blockers": [],
                "route_confidence": 0.58,
            }
        }
    )

    assert allowed is False
    assert "delivery_confirmations<3" in reasons
    assert "delivery_flow_confirmation_missing" in reasons


def test_heating_delivery_decision_allows_confirmed_heating_alert():
    allowed, reasons = heating_delivery_decision(
        {
            "route_decision": {
                "tier": "heating_up",
                "confirmations": ["buyer_breadth", "burst_strength", "market_support"],
                "blockers": [],
                "route_confidence": 0.72,
            }
        }
    )

    assert allowed is True
    assert "route_confidence:0.72" in reasons
    assert "market_support" in reasons


def test_observe_only_wallet_guard_blocks_candidate_and_heating_sends():
    candidate_allowed, candidate_reasons, _ = candidate_send_reasons(
        attention_score=0.82,
        creator_score=0.90,
        extra={
            "wallet_guard_watch_only": True,
            "attention_metrics": {"unique_buyers_5m": 8, "burst_count_60s": 10},
            "route_decision": {
                "tier": "heating_up",
                "confirmations": ["buyer_breadth", "burst_strength", "market_support"],
                "route_confidence": 0.80,
            },
        },
        dex_summary={"liquidity_usd": 40000.0, "txns_m5_buys": 18},
    )
    heating_allowed, heating_reasons = heating_delivery_decision(
        {
            "wallet_guard_watch_only": True,
            "route_decision": {
                "tier": "heating_up",
                "confirmations": ["buyer_breadth", "burst_strength", "market_support"],
                "route_confidence": 0.80,
            },
        }
    )

    assert candidate_allowed is False
    assert "wallet_guard_watch_only" in candidate_reasons
    assert heating_allowed is False
    assert "wallet_guard_watch_only" in heating_reasons

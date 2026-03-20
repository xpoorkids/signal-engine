from worker.signal_policy import (
    candidate_confirmation_signals,
    candidate_send_reasons,
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

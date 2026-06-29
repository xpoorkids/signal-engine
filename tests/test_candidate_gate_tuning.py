from worker.alert_gate import admission_check_candidate
from worker.promote import (
    _candidate_send_eligible,
    _wallet_cluster_review,
    _wallet_guard_category,
    _wallet_distribution_fail_reasons,
    _wallet_guard_observe_decision,
)
from app.services import signal_learning_service as sls


def test_candidate_dex_gate_rejects_weak_market_structure():
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.62,
        risk_score=0.30,
        extra={"metrics": {"age_minutes": 3.0}},
        dex_summary={
            "age_minutes": 3.0,
            "liquidity_usd": 3000,
            "volume_m5": 1500,
            "txns_m5_buys": 4,
            "txns_m5_sells": 8,
            "price_change_m5": 5.0,
        },
        attention_unavailable=False,
    )

    assert lifecycle == "dex"
    assert ok is False
    assert any(reason.startswith("dex_gate:") for reason in reasons)


def test_candidate_send_eligible_requires_real_attention_even_with_creator_quality():
    assert _candidate_send_eligible(0.20, 0.90) is False
    assert _candidate_send_eligible(0.36, 0.90) is True
    assert _candidate_send_eligible(0.50, 0.0) is True


def test_candidate_gate_uses_policy_override_thresholds():
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.17,
        risk_score=0.30,
        extra={"metrics": {"age_minutes": 0.4}, "bonding_curve_present": True},
        dex_summary=None,
        attention_unavailable=False,
        gate_config={
            "candidate_gate_attention_min": 0.14,
            "candidate_gate_min_age_sec": 15,
        },
        bonding_curve_verified=True,
    )

    assert lifecycle == "bonding_curve"
    assert ok is True
    assert reasons == []


def test_candidate_gate_rejects_unverified_token_target():
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.42,
        risk_score=0.20,
        extra={"metrics": {"age_minutes": 1.0}},
        dex_summary=None,
        attention_unavailable=False,
        token_is_tradeable=False,
    )

    assert lifecycle == "bonding_curve"
    assert ok is False
    assert "token_unverified" in reasons


def test_candidate_gate_rejects_unverified_bonding_curve_path():
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.42,
        risk_score=0.20,
        extra={"metrics": {"age_minutes": 1.0}},
        dex_summary=None,
        attention_unavailable=False,
        token_is_tradeable=True,
        bonding_curve_verified=False,
    )

    assert lifecycle == "bonding_curve"
    assert ok is False
    assert "bonding_curve_unverified" in reasons


def test_default_policy_descriptor_relaxes_candidate_gate_defaults(monkeypatch):
    monkeypatch.delenv("SIGNAL_ENGINE_CANDIDATE_GATE_ATTENTION_MIN", raising=False)
    monkeypatch.delenv("SIGNAL_ENGINE_CANDIDATE_GATE_MIN_AGE_SEC", raising=False)

    descriptor = sls._default_policy_descriptor()

    assert descriptor["candidate_gate_attention_min"] == 0.14
    assert descriptor["candidate_gate_min_age_sec"] == 15


def test_wallet_distribution_fail_reasons_flags_bundle_and_severe_concentration():
    reasons = _wallet_distribution_fail_reasons(
        {
            "risk": "high",
            "top_holder_pct": 0.36,
        },
        total_buys_30s=8,
        unique_wallets_30s=2,
        top_wallet_share=0.75,
    )

    assert "wallet_distribution_high_risk" in reasons
    assert "wallet_top_holder_concentration" in reasons
    assert "bundle_pattern_detected" in reasons


def test_wallet_distribution_fail_reasons_keeps_common_launch_concentration_out_of_hard_fail():
    reasons = _wallet_distribution_fail_reasons(
        {
            "risk": "high",
            "top_holder_pct": 0.22,
        },
        total_buys_30s=5,
        unique_wallets_30s=5,
        top_wallet_share=0.35,
    )

    assert reasons == []


def test_wallet_guard_observe_allows_confirmed_wallet_only_block():
    cluster = _wallet_cluster_review(
        {"risk": "high", "top_holder_pct": 0.36},
        total_buys_30s=7,
        unique_wallets_30s=5,
        top_wallet_share=0.42,
        attention_metrics={"unique_buyers_5m": 5, "burst_count_60s": 9},
        dex_summary={
            "liquidity_usd": 30000.0,
            "txns_m5_buys": 14,
            "txns_m5_sells": 8,
            "volume_m5": 25000.0,
            "price_change_m5": 12.0,
        },
        risk_score=0.50,
    )
    allowed, blockers = _wallet_guard_observe_decision(
        ["wallet_distribution_high_risk", "wallet_top_holder_concentration"],
        attention_score=0.52,
        risk_score=0.50,
        attention_metrics={"unique_buyers_5m": 5, "burst_count_60s": 9},
        dex_summary={
            "liquidity_usd": 30000.0,
            "txns_m5_buys": 14,
            "txns_m5_sells": 8,
            "volume_m5": 25000.0,
            "price_change_m5": 12.0,
        },
        wallet_cluster_review=cluster,
    )

    assert cluster["verdict"] == "coordinated_accumulation"
    assert allowed is True
    assert blockers == []


def test_wallet_cluster_review_blocks_toxic_bundle_shape():
    cluster = _wallet_cluster_review(
        {"risk": "high", "top_holder_pct": 0.48, "top10_pct": 0.76},
        total_buys_30s=8,
        unique_wallets_30s=2,
        top_wallet_share=0.78,
        attention_metrics={"unique_buyers_5m": 2, "burst_count_60s": 9},
        dex_summary={
            "liquidity_usd": 30000.0,
            "txns_m5_buys": 14,
            "txns_m5_sells": 8,
            "volume_m5": 25000.0,
            "price_change_m5": 12.0,
        },
        risk_score=0.50,
    )
    allowed, blockers = _wallet_guard_observe_decision(
        ["wallet_distribution_high_risk", "wallet_top_holder_concentration"],
        attention_score=0.70,
        risk_score=0.50,
        attention_metrics={"unique_buyers_5m": 8, "burst_count_60s": 12},
        dex_summary={
            "liquidity_usd": 30000.0,
            "txns_m5_buys": 14,
            "txns_m5_sells": 8,
            "volume_m5": 25000.0,
            "price_change_m5": 12.0,
        },
        wallet_cluster_review=cluster,
    )

    assert cluster["verdict"] == "toxic_cluster"
    assert allowed is False
    assert "wallet_cluster_toxic" in blockers


def test_wallet_guard_observe_blocks_mixed_or_weak_hard_fail():
    allowed, blockers = _wallet_guard_observe_decision(
        ["wallet_top_holder_concentration", "low_liquidity"],
        attention_score=0.80,
        risk_score=0.10,
        attention_metrics={"unique_buyers_5m": 10, "burst_count_60s": 12},
        dex_summary={
            "liquidity_usd": 30000.0,
            "txns_m5_buys": 14,
            "txns_m5_sells": 8,
            "volume_m5": 25000.0,
            "price_change_m5": 12.0,
        },
    )

    assert allowed is False
    assert blockers == ["non_wallet_hard_fail"]


def test_wallet_guard_v2_categories_separate_fraud_and_accumulation():
    assert _wallet_guard_category(["mint_authority_active"]) == "hard_fraud"
    assert (
        _wallet_guard_category(
            ["wallet_top_holder_concentration"],
            wallet_observe_ok=True,
            attention_metrics={"tracked_wallet_hits": 1},
        )
        == "smart_accumulation"
    )
    assert _wallet_guard_category(["wallet_distribution_high_risk"]) == "early_concentration"
    assert (
        _wallet_guard_category(
            ["wallet_distribution_high_risk"],
            wallet_cluster_review={"verdict": "coordinated_accumulation"},
        )
        == "coordinated_accumulation"
    )
    assert (
        _wallet_guard_category(
            ["wallet_distribution_high_risk"],
            wallet_cluster_review={"verdict": "toxic_cluster"},
        )
        == "toxic_wallet_cluster"
    )
    assert _wallet_guard_category(["liquidity_unknown"]) == "unknown_wallet_structure"

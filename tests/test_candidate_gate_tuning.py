from worker.alert_gate import admission_check_candidate
from worker.promote import (
    _candidate_send_eligible,
    _candidate_gate_skip_should_mature,
    _candidate_maturation_watch_signal,
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


def test_candidate_dex_gate_accepts_scanner_metric_aliases():
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.62,
        risk_score=0.30,
        extra={"metrics": {"age_minutes": 3.0}},
        dex_summary={
            "age_minutes": 3.0,
            "liquidity_usd": 36_000.0,
            "volume_m5_usd": 21_000.0,
            "buys_5m": 958,
            "sells_5m": 174,
            "price_change_5m": 7.0,
            "market_cap_usd": 186_000.0,
        },
        attention_unavailable=False,
    )

    assert lifecycle == "dex"
    assert ok is True
    assert reasons == []


def test_candidate_dex_gate_bypasses_age_for_high_conviction_breadth_proxy():
    extra = {
        "metrics": {
            "age_minutes": 0.05,
            "unique_buyers_5m": 0,
            "burst_count_60s": 0,
            "tracked_wallet_hits": 0,
            "kol_wallet_hits": 0,
        }
    }
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.20,
        risk_score=0.0,
        extra=extra,
        dex_summary={
            "age_minutes": 0.05,
            "liquidity_usd": 831_000.0,
            "volume_m5": 5_350.0,
            "txns_m5_buys": 298,
            "txns_m5_sells": 5,
            "price_change_m5": 1.06,
            "market_cap_usd": 925_000.0,
        },
        attention_unavailable=False,
        gate_config={
            "candidate_gate_attention_min": 0.14,
            "candidate_gate_min_age_sec": 15,
        },
    )

    assert lifecycle == "dex"
    assert ok is True
    assert reasons == []
    assert extra["candidate_age_bypass_reason"] == "winner_breadth_proxy"
    assert extra["candidate_admission_proxy_bypass"] == [
        "age<15s",
        "dex_gate:vol5m<12000.0",
    ]
    assert "winner_breadth_proxy" in extra["candidate_confirmation_signals"]


def test_candidate_dex_gate_allows_thin_ignition_breadth_proxy():
    extra = {
        "metrics": {
            "age_minutes": 0.5,
            "unique_buyers_5m": 0,
            "burst_count_60s": 0,
            "tracked_wallet_hits": 0,
            "kol_wallet_hits": 0,
        }
    }
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.20,
        risk_score=0.0,
        extra=extra,
        dex_summary={
            "age_minutes": 0.5,
            "liquidity_usd": 90_000.0,
            "volume_m5": 3_800.0,
            "txns_m5_buys": 190,
            "txns_m5_sells": 48,
            "price_change_m5": 8.4,
            "market_cap_usd": 640_000.0,
        },
        attention_unavailable=False,
        gate_config={
            "candidate_gate_attention_min": 0.14,
            "candidate_gate_min_age_sec": 15,
        },
    )

    assert lifecycle == "dex"
    assert ok is True
    assert reasons == []
    assert extra["candidate_admission_proxy_bypass"] == ["dex_gate:vol5m<12000.0"]
    assert "winner_breadth_proxy" in extra["candidate_confirmation_signals"]
    assert "thin_ignition_watch" in extra["candidate_confirmation_signals"]


def test_candidate_dex_gate_allows_repeated_accumulation_watch():
    extra = {
        "metrics": {
            "age_minutes": 180.0,
            "dex_scan_persistent": True,
            "dex_scan_repeat_count": 6,
            "dex_scan_volume_delta_5m": 750.0,
            "independent_flow_confirmed": True,
        }
    }
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.20,
        risk_score=0.0,
        extra=extra,
        dex_summary={
            "age_minutes": 180.0,
            "liquidity_usd": 80_000.0,
            "volume_m5": 2_800.0,
            "txns_m5_buys": 42,
            "txns_m5_sells": 31,
            "price_change_m5": 3.0,
            "market_cap_usd": 1_200_000.0,
        },
        attention_unavailable=False,
    )

    assert lifecycle == "dex"
    assert ok is True
    assert reasons == []
    assert extra["candidate_admission_watch_bypass"] == [
        "dex_gate:vol5m<5000.0",
        "confirmation_signals<2",
    ]
    assert "dex_accumulation_watch" in extra["candidate_confirmation_signals"]


def test_candidate_dex_gate_keeps_weak_accumulation_rejected():
    extra = {
        "metrics": {
            "age_minutes": 180.0,
            "dex_scan_persistent": True,
            "dex_scan_repeat_count": 6,
            "dex_scan_volume_delta_5m": 750.0,
            "independent_flow_confirmed": True,
        }
    }
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.20,
        risk_score=0.0,
        extra=extra,
        dex_summary={
            "age_minutes": 180.0,
            "liquidity_usd": 80_000.0,
            "volume_m5": 2_800.0,
            "txns_m5_buys": 42,
            "txns_m5_sells": 60,
            "price_change_m5": 3.0,
            "market_cap_usd": 1_200_000.0,
        },
        attention_unavailable=False,
    )

    assert lifecycle == "dex"
    assert ok is False
    assert "dex_gate:vol5m<5000.0" in reasons


def test_candidate_dex_gate_allows_curated_accumulation_after_burst():
    extra = {
        "metrics": {
            "age_minutes": 430.0,
            "community_takeover": True,
            "discovery_sources": ["community_takeover"],
            "dex_scan_persistent": True,
            "dex_scan_repeat_count": 11,
            "dex_scan_volume_delta_5m": -180.0,
            "independent_flow_confirmed": False,
        }
    }
    ok, reasons, lifecycle = admission_check_candidate(
        attention_score=0.20,
        risk_score=0.0,
        extra=extra,
        dex_summary={
            "age_minutes": 430.0,
            "liquidity_usd": 31_000.0,
            "volume_m5": 2_200.0,
            "txns_m5_buys": 34,
            "txns_m5_sells": 16,
            "price_change_m5": -6.6,
            "market_cap_usd": 142_000.0,
        },
        attention_unavailable=False,
    )

    assert lifecycle == "dex"
    assert ok is True
    assert reasons == []
    assert extra["candidate_admission_watch_bypass"] == [
        "dex_gate:vol5m<5000.0",
        "confirmation_signals<2",
    ]


def test_candidate_send_eligible_requires_real_attention_even_with_creator_quality():
    assert _candidate_send_eligible(0.20, 0.90) is False
    assert _candidate_send_eligible(0.36, 0.90) is True
    assert _candidate_send_eligible(0.50, 0.0) is True


def test_candidate_gate_skip_matures_only_transient_dex_reasons():
    assert _candidate_gate_skip_should_mature(
        ["age<15s", "attention<0.14", "dex_gate:vol5m<5000.0", "confirmation_signals<2"],
        lifecycle="dex",
    ) is True
    assert _candidate_gate_skip_should_mature(["age<15s"], lifecycle="bonding_curve") is False
    assert _candidate_gate_skip_should_mature(
        ["age<15s", "wallet_distribution_high_risk"],
        lifecycle="dex",
    ) is False
    assert _candidate_gate_skip_should_mature(
        ["age<15s", "dex_gate:price_change_5m<-18.0"],
        lifecycle="dex",
    ) is False


def test_candidate_maturation_watch_accepts_thin_ignition_near_pass():
    ok, signals = _candidate_maturation_watch_signal(
        ["age<15s", "attention<0.14", "dex_gate:vol5m<5000.0", "confirmation_signals<2"],
        lifecycle="dex",
        attention_score=0.13,
        risk_score=0.05,
        extra={
            "candidate_confirmation_signals": ["winner_breadth_proxy", "thin_ignition_watch"],
        },
        dex_summary={
            "liquidity_usd": 140_000.0,
            "volume_m5": 3_700.0,
            "txns_m5_buys": 164,
            "txns_m5_sells": 42,
            "price_change_m5": 8.2,
            "market_cap_usd": 620_000.0,
        },
    )

    assert ok is True
    assert "candidate_maturation_watch" not in signals
    assert "thin_ignition_watch" in signals
    assert "pre_volume_breakout" in signals


def test_candidate_maturation_watch_rejects_hard_risk_skip():
    ok, signals = _candidate_maturation_watch_signal(
        ["age<15s", "wallet_distribution_high_risk"],
        lifecycle="dex",
        attention_score=0.13,
        risk_score=0.05,
        extra={"candidate_confirmation_signals": ["thin_ignition_watch"]},
        dex_summary={
            "liquidity_usd": 140_000.0,
            "volume_m5": 3_700.0,
            "txns_m5_buys": 164,
            "txns_m5_sells": 42,
            "price_change_m5": 8.2,
            "market_cap_usd": 620_000.0,
        },
    )

    assert ok is False
    assert signals == []


def test_candidate_maturation_watch_rejects_overextended_move():
    ok, signals = _candidate_maturation_watch_signal(
        ["age<15s", "dex_gate:vol5m<5000.0", "confirmation_signals<2"],
        lifecycle="dex",
        attention_score=0.13,
        risk_score=0.05,
        extra={"candidate_confirmation_signals": ["thin_ignition_watch"]},
        dex_summary={
            "liquidity_usd": 140_000.0,
            "volume_m5": 3_700.0,
            "txns_m5_buys": 164,
            "txns_m5_sells": 42,
            "price_change_m5": 42.0,
            "market_cap_usd": 620_000.0,
        },
    )

    assert ok is False
    assert "pre_volume_breakout" not in signals


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


def test_wallet_distribution_fail_reasons_flags_identity_bundle():
    reasons = _wallet_distribution_fail_reasons(
        {
            "risk": "ok",
            "top_holder_pct": 0.04,
        },
        total_buys_30s=8,
        unique_wallets_30s=6,
        top_wallet_share=0.30,
        unique_wallet_clusters_30s=2,
        top_wallet_cluster_share=0.75,
    )

    assert reasons == ["wallet_identity_bundle_pattern"]


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


def test_wallet_cluster_review_blocks_known_toxic_identity_even_with_wallet_breadth():
    cluster = _wallet_cluster_review(
        {"risk": "ok", "top_holder_pct": 0.06, "top10_pct": 0.22},
        total_buys_30s=8,
        unique_wallets_30s=6,
        top_wallet_share=0.30,
        unique_wallet_clusters_30s=2,
        top_wallet_cluster_share=0.75,
        wallet_identity={
            "cluster_ids": ["rug-team-1"],
            "clusters": [{"cluster_id": "rug-team-1", "reputation": "toxic_history"}],
            "summary": {"toxic_clusters": 1, "winner_clusters": 0, "mixed_clusters": 0},
        },
        attention_metrics={"unique_buyers_5m": 8, "burst_count_60s": 9},
        dex_summary={
            "liquidity_usd": 30000.0,
            "txns_m5_buys": 14,
            "txns_m5_sells": 4,
            "volume_m5": 18000.0,
            "price_change_m5": 12.0,
        },
        risk_score=0.30,
    )

    assert cluster["verdict"] == "toxic_cluster"
    assert "wallet_cluster_toxic_history" in cluster["blockers"]
    assert cluster["metrics"]["wallet_identity_cluster_ids"] == ["rug-team-1"]


def test_wallet_cluster_review_uses_winner_history_only_with_constructive_flow():
    cluster = _wallet_cluster_review(
        {"risk": "ok", "top_holder_pct": 0.06, "top10_pct": 0.22},
        total_buys_30s=8,
        unique_wallets_30s=6,
        top_wallet_share=0.30,
        unique_wallet_clusters_30s=5,
        top_wallet_cluster_share=0.35,
        wallet_identity={
            "cluster_ids": ["smart-1"],
            "clusters": [{"cluster_id": "smart-1", "reputation": "winner_history"}],
            "summary": {"toxic_clusters": 0, "winner_clusters": 1, "mixed_clusters": 0},
        },
        attention_metrics={"unique_buyers_5m": 8, "burst_count_60s": 9},
        dex_summary={
            "liquidity_usd": 30000.0,
            "txns_m5_buys": 14,
            "txns_m5_sells": 4,
            "volume_m5": 18000.0,
            "price_change_m5": 12.0,
        },
        risk_score=0.30,
    )

    assert cluster["verdict"] == "smart_accumulation"
    assert "wallet_cluster_winner_history" in cluster["signals"]


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

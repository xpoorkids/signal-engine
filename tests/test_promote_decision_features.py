import json

from app.services import signal_learning_service as sls
from worker.events import Event
from worker.promote import _record_decision, _wallet_cluster_review, _wallet_guard_observe_decision


def test_record_decision_persists_tradeability_and_ev_features(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    event = Event(
        type="candidate",
        source="test",
        token="token-ev-features",
        confidence=0.72,
        ts=1_800_000_000,
        extra={
            "dex_summary": {
                "market_cap_usd": 125000.0,
                "price_usd": 0.0042,
                "liquidity_usd": 55000.0,
                "volume_m5": 18000.0,
                "volume_h1": 92000.0,
                "txns_h1_buys": 220,
                "txns_h1_sells": 88,
            },
            "trade_validation": {
                "approved": True,
                "intended_size_usd": 250.0,
                "pair_address": "pair-1",
                "dex_id": "raydium",
                "reasons": [],
                "warnings": ["quote_provider_reserve"],
                "buy_quote": {
                    "slippage_bps": 105.0,
                    "price_impact_pct": 0.7,
                    "route_labels": ["Raydium"],
                },
                "sell_quote": {
                    "slippage_bps": 135.0,
                    "price_impact_pct": 0.9,
                    "route_labels": ["Raydium"],
                },
            },
            "candidate_ev": {
                "approved": True,
                "net_edge_bps": 640.0,
                "gross_upside_bps": 960.0,
                "cost_bps": 240.0,
                "risk_penalty_bps": 80.0,
                "round_trip_slippage_bps": 240.0,
                "max_price_impact_pct": 0.9,
                "reasons": ["ev_gate_passed"],
            },
            "candidate_send_eligible": True,
            "candidate_send": False,
            "candidate_rate_limit_allowed": False,
            "candidate_rate_limit_checked": True,
            "candidate_progression_ok": True,
            "candidate_admission_watch_bypass": [
                "dex_gate:vol5m<5000.0",
                "confirmation_signals<2",
            ],
            "wallet_cluster_review": {
                "verdict": "coordinated_accumulation",
                "score": 43,
                "signals": ["wallet_cluster_buyer_breadth"],
                "blockers": ["wallet_cluster_top_holder_watch"],
                "metrics": {"top_holder_pct": 0.36, "unique_buyers_5m": 6},
            },
        },
    )

    _record_decision(
        event,
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        attention_score=0.72,
        risk_score=0.20,
        confidence_score=0.72,
        creator_score=0.80,
        lifecycle="dex",
    )

    with sls._connect() as c:
        row = c.execute(
            "SELECT features_json FROM signal_decisions WHERE token=?",
            ("token-ev-features",),
        ).fetchone()

    assert row is not None
    features = json.loads(row[0])
    assert features["market_cap_usd"] == 125000.0
    assert features["price_usd"] == 0.0042
    assert features["volume_h1_usd"] == 92000.0
    assert features["trade_validation_approved"] is True
    assert features["trade_validation_warnings"] == ["quote_provider_reserve"]
    assert features["buy_slippage_bps"] == 105.0
    assert features["sell_slippage_bps"] == 135.0
    assert features["route_labels"] == ["Raydium"]
    assert features["wallet_cluster_verdict"] == "coordinated_accumulation"
    assert features["wallet_cluster_score"] == 43
    assert features["wallet_cluster_signals"] == ["wallet_cluster_buyer_breadth"]
    assert features["wallet_cluster_blockers"] == ["wallet_cluster_top_holder_watch"]
    assert features["wallet_cluster_metrics"]["top_holder_pct"] == 0.36
    assert features["candidate_ev_approved"] is True
    assert features["candidate_ev_net_edge_bps"] == 640.0
    assert features["candidate_send_eligible"] is True
    assert features["candidate_send_final"] is False
    assert features["candidate_rate_limit_allowed"] is False
    assert features["candidate_rate_limit_checked"] is True
    assert features["candidate_progression_ok"] is True
    assert features["candidate_admission_watch_bypass"] == [
        "dex_gate:vol5m<5000.0",
        "confirmation_signals<2",
    ]


def test_wallet_cluster_constructive_single_holder_can_enter_observe_review():
    attention_metrics = {
        "unique_buyers_5m": 7,
        "burst_count_60s": 9,
        "tracked_wallet_hits": 0,
        "kol_wallet_hits": 0,
    }
    dex_summary = {
        "liquidity_usd": 25_000.0,
        "txns_m5_buys": 14,
        "txns_m5_sells": 6,
        "volume_m5": 45_000.0,
        "price_change_m5": 12.0,
    }
    cluster = _wallet_cluster_review(
        {"risk": "high", "top_holder_pct": 0.48, "top10_pct": 0.58},
        total_buys_30s=5,
        unique_wallets_30s=4,
        top_wallet_share=0.52,
        attention_metrics=attention_metrics,
        dex_summary=dex_summary,
        risk_score=0.32,
    )

    observe_ok, blockers = _wallet_guard_observe_decision(
        ["wallet_distribution_high_risk", "wallet_top_holder_concentration"],
        attention_score=0.48,
        risk_score=0.32,
        attention_metrics=attention_metrics,
        dex_summary=dex_summary,
        wallet_cluster_review=cluster,
    )

    assert cluster["verdict"] == "coordinated_accumulation"
    assert "wallet_cluster_single_holder_dominant" in cluster["blockers"]
    assert observe_ok is True
    assert blockers == []


def test_wallet_cluster_single_holder_stays_toxic_with_sell_pressure():
    cluster = _wallet_cluster_review(
        {"risk": "high", "top_holder_pct": 0.48, "top10_pct": 0.58},
        total_buys_30s=5,
        unique_wallets_30s=4,
        top_wallet_share=0.52,
        attention_metrics={"unique_buyers_5m": 7, "burst_count_60s": 9},
        dex_summary={
            "liquidity_usd": 25_000.0,
            "txns_m5_buys": 10,
            "txns_m5_sells": 16,
            "volume_m5": 45_000.0,
            "price_change_m5": 12.0,
        },
        risk_score=0.32,
    )

    observe_ok, blockers = _wallet_guard_observe_decision(
        ["wallet_distribution_high_risk", "wallet_top_holder_concentration"],
        attention_score=0.48,
        risk_score=0.32,
        attention_metrics={"unique_buyers_5m": 7, "burst_count_60s": 9},
        dex_summary={
            "liquidity_usd": 25_000.0,
            "txns_m5_buys": 10,
            "txns_m5_sells": 16,
            "volume_m5": 45_000.0,
            "price_change_m5": 12.0,
        },
        wallet_cluster_review=cluster,
    )

    assert cluster["verdict"] == "toxic_cluster"
    assert "wallet_cluster_sell_pressure_high" in cluster["blockers"]
    assert observe_ok is False
    assert "wallet_cluster_toxic" in blockers

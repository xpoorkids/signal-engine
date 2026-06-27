import json

from app.services import signal_learning_service as sls
from worker.events import Event
from worker.promote import _record_decision


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
    assert features["candidate_ev_approved"] is True
    assert features["candidate_ev_net_edge_bps"] == 640.0

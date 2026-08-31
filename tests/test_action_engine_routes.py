import pytest
from fastapi.testclient import TestClient as FastAPITestClient

from app import main


OPERATOR_TOKEN = "test-operator-token"


@pytest.fixture(autouse=True)
def _configure_operator_auth(monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_OPERATOR_API_TOKEN", OPERATOR_TOKEN)


def TestClient(app, **kwargs):
    headers = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    headers.update(kwargs.pop("headers", {}))
    return FastAPITestClient(app, headers=headers, **kwargs)


def test_position_routes_manual_buy_sell_history_reopen_and_recommendation(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "routes.db"))
    client = TestClient(main.app)

    bought = client.post(
        "/positions/manual/buy",
        json={"token": "token-route", "symbol": "RTR", "token_quantity": 100, "gross_usd": 100, "fees_usd": 2},
    )
    assert bought.status_code == 200
    position = bought.json()
    position_id = position["position_id"]

    add = client.post(f"/positions/{position_id}/buy", json={"token_quantity": 50, "gross_usd": 75, "fees_usd": 1})
    assert add.status_code == 200
    assert add.json()["current_token_quantity"] == 150

    sell = client.post(f"/positions/{position_id}/sell", json={"token_quantity": 25, "gross_usd": 50, "fees_usd": 1})
    assert sell.status_code == 200
    assert sell.json()["realized_proceeds_usd"] == 49

    update = client.patch(f"/positions/{position_id}", json={"risk_profile": "aggressive", "exit_style": "catalyst_runner"})
    assert update.status_code == 200

    history = client.get(f"/positions/{position_id}/history")
    assert history.status_code == 200
    assert len(history.json()["fills"]) == 3

    rec = client.post(
        f"/positions/{position_id}/recommendation",
        json={"market": {"current_executable_value_usd": 260, "executable_net_sell_value_per_token": 2.08, "sell_route_ok": True, "sell_impact_pct": 2}},
    )
    assert rec.status_code == 200
    assert rec.json()["execution_mode"] == "manual"
    assert rec.json()["calibration_status"] == "HEURISTIC_UNCALIBRATED"

    assert client.post(f"/positions/{position_id}/close").json()["status"] == "closed"
    assert client.post(f"/positions/{position_id}/reopen").json()["status"] == "open"


def test_catalyst_routes_and_token_recommendation(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "routes.db"))
    client = TestClient(main.app)

    catalyst = client.post(
        "/catalysts",
        json={
            "token": "token-cat-route",
            "title": "Partnership announced",
            "verification_status": "active",
            "secondary_confirmations": ["source-a", "source-b"],
            "catalyst_confidence_pct": 88,
            "catalyst_flow_confirmation": True,
        },
    )
    assert catalyst.status_code == 200
    catalyst_id = catalyst.json()["catalyst_id"]

    rec = client.post(
        "/actions/recommendation",
        json={
            "token": "token-cat-route",
            "market": {
                "liquidity_usd": 50000,
                "volume_m5": 30000,
                "txns_m5_buys": 35,
                "txns_m5_sells": 10,
                "buy_route_ok": True,
                "sell_route_ok": True,
                "quote_fresh": True,
                "organic_flow_windows": 2,
                "wallet_or_fee_confirmation": True,
            },
            "assessment": {"attention_score": 0.9, "risk_score": 0.15, "rug_check": {"verdict": "low"}, "security": {}},
            "catalyst": catalyst.json(),
            "intended_size_usd": 250,
        },
    )
    assert rec.status_code == 200
    assert rec.json()["action"] == "CATALYST BUY NOW"

    invalid = client.post(f"/catalysts/{catalyst_id}/invalid", json={"reason": "source retracted"})
    assert invalid.status_code == 200
    assert invalid.json()["verification_status"] == "invalidated"


def test_existing_manual_review_route_can_include_action_recommendation_when_enabled(tmp_path, monkeypatch):
    from app.services import review_service

    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "routes.db"))
    monkeypatch.setenv("SIGNAL_ENGINE_ACTION_ENGINE_ENABLED", "1")
    token = "11111111111111111111111111111111"

    async def dex_enrich(_token):
        return {"pairs": []}

    monkeypatch.setattr(review_service, "fetch_token_metadata", lambda _token: {"symbol": "TOK", "name": "Token"})
    monkeypatch.setattr(review_service, "dex_enrich_token", dex_enrich)
    monkeypatch.setattr(review_service, "select_best_pair", lambda _data, _token: {"pair": "ok"})
    monkeypatch.setattr(
        review_service,
        "summarize_pair",
        lambda _pair: {"liquidity_usd": 50000, "volume_m5": 25000, "txns_m5_buys": 30, "txns_m5_sells": 10, "price_change_m5": 5, "age_minutes": 12},
    )
    monkeypatch.setattr(review_service.ELITE, "auth_check", lambda _token: (False, False))
    monkeypatch.setattr(review_service.ELITE, "liq_check", lambda _token, _summary: (50000, True, False))
    monkeypatch.setattr(review_service.ELITE, "compute_elite_score", lambda **_kwargs: 8)
    monkeypatch.setattr(review_service, "wallet_risk_score", lambda _token: {"risk": "ok", "top_holder_pct": 0.04})
    monkeypatch.setattr(review_service, "fetch_x_signal", lambda *_args: {"tweet_count": 20, "unique_authors": 15, "likes": 100})
    monkeypatch.setattr(review_service, "format_discord", lambda _event: {"embeds": []})

    client = TestClient(main.app)
    response = client.get(f"/review/{token}?format=json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["manual_buy_assessment"]["action"] == "VALIDATED_WATCH"
    assert payload["action_recommendation"]["execution_mode"] == "manual"
    assert payload["action_recommendation"]["calibration_status"] == "HEURISTIC_UNCALIBRATED"

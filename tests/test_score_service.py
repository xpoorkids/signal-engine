from datetime import datetime, timezone

from app.routes.score import score
from app.services.score_service import _pick_contract_address, score_pairs


def test_pick_contract_address_prefers_quote_token_when_base_is_wsol():
    pump_token = "D69bugFJG4y3kmJxiLPTMuqqK3e3PnJHQCcQvALgpump"
    pair = {
        "baseToken": {
            "address": "So11111111111111111111111111111111111111112",
            "symbol": "SOL",
        },
        "quoteToken": {
            "address": pump_token,
            "symbol": "BUG",
        },
    }

    assert _pick_contract_address(pair) == pump_token


def test_score_pairs_returns_pump_contract_not_base_pair_leg():
    pump_token = "D69bugFJG4y3kmJxiLPTMuqqK3e3PnJHQCcQvALgpump"
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    pairs = [
        {
            "baseToken": {
                "address": "So11111111111111111111111111111111111111112",
                "symbol": "SOL",
            },
            "quoteToken": {
                "address": pump_token,
                "symbol": "BUG",
            },
            "liquidity": {"usd": 1200},
            "volume": {"m5": 75},
            "priceChange": {"m5": 18},
            "pairCreatedAt": now_ms - 20_000,
        }
    ]

    scored = score_pairs(pairs)

    assert scored[0]["token"] == pump_token
    assert scored[0]["symbol"] == "BUG"


def test_score_route_persists_contract_before_symbol(monkeypatch):
    appended = {}

    def _fake_score_token(_: dict) -> dict:
        return {
            "status": "WATCH",
            "score": 1.0,
            "reasons": ["watch"],
            "candidate": {
                "symbol": "BUG",
                "address": "D69bugFJG4y3kmJxiLPTMuqqK3e3PnJHQCcQvALgpump",
                "chain": "sol",
            },
        }

    def _fake_append_watch_event(payload: dict) -> None:
        appended.update(payload)

    monkeypatch.setattr("app.routes.score.score_token", _fake_score_token)
    monkeypatch.setattr("app.routes.score.append_watch_event", _fake_append_watch_event)

    score({})

    assert appended["token"] == "D69bugFJG4y3kmJxiLPTMuqqK3e3PnJHQCcQvALgpump"

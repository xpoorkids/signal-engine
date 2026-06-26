from datetime import datetime, timezone

from app.routes.score import score
from app.services.score_service import _is_solana_pair, _pick_contract_address, score_pairs


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
            "chainId": "solana",
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
    assert scored[0]["chain"] == "sol"


def test_is_solana_pair_accepts_solana_chain_values():
    assert _is_solana_pair({"chainId": "solana"}) is True
    assert _is_solana_pair({"chain": "sol"}) is True
    assert _is_solana_pair({"chainId": "ethereum"}) is False
    assert _is_solana_pair({}) is False


def test_score_pairs_skips_non_solana_pairs():
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    pairs = [
        {
            "chainId": "ethereum",
            "baseToken": {
                "address": "0x1234567890abcdef1234567890abcdef12345678",
                "symbol": "ETHX",
            },
            "quoteToken": {
                "address": "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
                "symbol": "WETH",
            },
            "liquidity": {"usd": 5000},
            "volume": {"m5": 500},
            "priceChange": {"m5": 25},
            "pairCreatedAt": now_ms - 20_000,
        },
        {
            "chainId": "base",
            "baseToken": {
                "address": "0x2222222222222222222222222222222222222222",
                "symbol": "BASEX",
            },
            "quoteToken": {
                "address": "0x3333333333333333333333333333333333333333",
                "symbol": "WETH",
            },
            "liquidity": {"usd": 5000},
            "volume": {"m5": 500},
            "priceChange": {"m5": 25},
            "pairCreatedAt": now_ms - 20_000,
        },
    ]

    assert score_pairs(pairs) == []


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


def test_pick_contract_address_does_not_fall_back_to_excluded_quote():
    pair = {
        "baseToken": {
            "address": "7vfCXTUXx5W7D3eK7oN7htTz3h6m5r3WQYgbR1fRpump",
            "symbol": "TEST",
        },
        "quoteToken": {
            "address": "So11111111111111111111111111111111111111112",
            "symbol": "SOL",
        },
    }

    assert _pick_contract_address(pair) == "7vfCXTUXx5W7D3eK7oN7htTz3h6m5r3WQYgbR1fRpump"


def test_score_route_does_not_persist_symbol_as_token(monkeypatch):
    appended = []

    def _fake_score_token(_: dict) -> dict:
        return {
            "status": "WATCH",
            "score": 0.8,
            "reasons": ["watch"],
            "candidate": {
                "symbol": "BUG",
                "chain": "sol",
            },
        }

    def _fake_append_watch_event(payload: dict) -> None:
        appended.append(payload)

    monkeypatch.setattr("app.routes.score.score_token", _fake_score_token)
    monkeypatch.setattr("app.routes.score.append_watch_event", _fake_append_watch_event)

    score({})

    assert appended == []

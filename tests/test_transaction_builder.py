from worker.events import Event
from worker.transaction_builder import (
    build_execution_request,
    build_transaction_intent,
)


def _validation_payload() -> dict:
    return {
        "approved": True,
        "validated_ts": 2_000_000_000,
        "quote_expires_ts": 2_000_000_015,
        "intended_size_usd": 100.0,
        "market_target": "dex",
        "pair_address": "pair-1",
        "dex_id": "raydium",
        "buy_quote": {
            "provider": "jupiter",
            "route_exists": True,
            "amount_in": 100.0,
            "amount_in_units": "usd",
            "expected_output_tokens": 180.0,
            "expected_output_usd": 99.4,
            "execution_price_usd": 0.555,
            "slippage_bps": 120.0,
            "quote_context_slot": 123,
            "quote_time_taken": 0.12,
            "price_impact_pct": 0.7,
            "route_labels": ["Raydium"],
        },
        "sell_quote": {
            "provider": "jupiter",
            "route_exists": True,
            "amount_in": 180.0,
            "amount_in_units": "token",
            "expected_output_usd": 96.5,
            "expected_output_tokens": 180.0,
            "execution_price_usd": 0.536,
            "slippage_bps": 140.0,
            "quote_context_slot": 124,
            "quote_time_taken": 0.09,
            "price_impact_pct": 0.9,
            "route_labels": ["Raydium"],
        },
        "checks": [
            {"name": "buy_slippage_bps", "threshold": 250},
            {"name": "sell_slippage_bps", "threshold": 350},
            {"name": "market_data_age_sec", "threshold": 20},
        ],
        "market_data": {
            "snapshot_ts": 2_000_000_000,
            "age_sec": 0.0,
            "quote_ttl_sec": 15,
            "quote_provider": "hybrid",
            "require_venue_quotes": True,
        },
        "risk_summary": {
            "risk_score": 0.2,
        },
    }


def test_build_transaction_intent_is_deterministic():
    event = Event(
        type="promoted",
        source="engine",
        token="token-1",
        extra={"_signal_id": "sig-1"},
    )
    request = build_execution_request(event=event, validation=_validation_payload())

    intent_a = build_transaction_intent(request)
    intent_b = build_transaction_intent(request)

    assert intent_a.intent_id == intent_b.intent_id
    assert intent_a.route.buy_provider == "jupiter"
    assert intent_a.route.sell_provider == "jupiter"
    assert intent_a.quote.quote_expires_ts == 2_000_000_015
    assert intent_a.quote.quote_ttl_sec == 15
    assert intent_a.slippage.max_buy_slippage_bps == 250
    assert intent_a.slippage.max_sell_slippage_bps == 350
    assert intent_a.constraints.no_signing is True
    assert intent_a.constraints.no_broadcast is True
    assert intent_a.constraints.require_sell_route is True


def test_build_execution_request_rejects_unapproved_validation():
    event = Event(type="promoted", source="engine", token="token-1")
    validation = _validation_payload()
    validation["approved"] = False

    try:
        build_execution_request(event=event, validation=validation)
    except ValueError as ex:
        assert str(ex) == "validation_not_approved"
    else:
        raise AssertionError("expected validation_not_approved")


def test_build_transaction_intent_rejects_missing_sell_quote():
    event = Event(type="promoted", source="engine", token="token-1")
    validation = _validation_payload()
    validation["sell_quote"] = {}

    try:
        build_execution_request(event=event, validation=validation)
    except ValueError as ex:
        assert str(ex) == "sell_quote_missing"
    else:
        raise AssertionError("expected sell_quote_missing")

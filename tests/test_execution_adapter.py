from worker.execution_adapter import advance_submission_attempt, create_submission_attempt
from worker.transaction_builder import build_execution_request, build_transaction_intent
from worker.events import Event


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
        },
    }


def _transaction_intent():
    event = Event(type="promoted", source="engine", token="token-1", extra={"_signal_id": "sig-1"})
    request = build_execution_request(event=event, validation=_validation_payload())
    return build_transaction_intent(request)


def test_create_submission_attempt_carries_intent_constraints():
    attempt = create_submission_attempt(transaction_intent=_transaction_intent(), now_ts=2_000_000_000)

    assert attempt.adapter_kind == "shadow_submission_simulator"
    assert attempt.intent_id
    assert attempt.request_id
    assert attempt.status == "submit_requested"
    assert attempt.quote_expires_ts == 2_000_000_015
    assert attempt.constraints.no_signing is True
    assert attempt.constraints.no_broadcast is True
    assert attempt.constraints.require_fresh_quotes is True


def test_advance_submission_attempt_preserves_request_and_can_land():
    intent = _transaction_intent()
    created = create_submission_attempt(transaction_intent=intent, now_ts=2_000_000_000)

    acked = advance_submission_attempt(transaction_intent=intent, attempt=created.as_dict(), now_ts=2_000_000_001)
    landed = advance_submission_attempt(transaction_intent=intent, attempt=created.as_dict(), now_ts=2_000_000_003)

    assert acked.request_id == created.request_id
    assert acked.status in {"submit_requested", "submit_acked"}
    assert landed.request_id == created.request_id
    assert landed.status in {"submit_acked", "landed"}


def test_advance_submission_attempt_expires_on_quote_deadline():
    intent = _transaction_intent()
    created = create_submission_attempt(transaction_intent=intent, now_ts=2_000_000_000)

    expired = advance_submission_attempt(transaction_intent=intent, attempt=created.as_dict(), now_ts=2_000_000_020)

    assert expired.request_id == created.request_id
    assert expired.status == "submit_expired"
    assert expired.terminal is True
    assert expired.reason == "quote_expired"

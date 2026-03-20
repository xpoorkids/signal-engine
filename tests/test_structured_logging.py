import logging

from app.services.structured_logging import log_event, structured_log_message


def test_structured_log_message_renders_grep_friendly_fields():
    message = structured_log_message(
        "decision",
        token="abc123",
        success=True,
        reasons=["gate_pass", "risk_ok"],
        route={"tier": "sniper", "ttl": 90},
        note="needs review",
    )

    assert message.startswith("[decision]")
    assert "token=abc123" in message
    assert "success=true" in message
    assert 'reasons=["gate_pass","risk_ok"]' in message
    assert 'route={"tier":"sniper","ttl":90}' in message
    assert 'note="needs review"' in message


def test_log_event_emits_structured_message(caplog):
    logger = logging.getLogger("tests.structured_logging")
    caplog.set_level(logging.INFO, logger="tests.structured_logging")

    log_event(logger, logging.INFO, "dispatch", token="abc456", delivered=False, reason="cooldown")

    assert "[dispatch]" in caplog.text
    assert "token=abc456" in caplog.text
    assert "delivered=false" in caplog.text
    assert "reason=cooldown" in caplog.text

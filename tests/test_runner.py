from worker.events import Event
from worker import runner


def test_persist_non_candidate_delivery_skips_when_not_delivered(monkeypatch):
    recorded: list[Event] = []

    monkeypatch.setattr(runner, "record_signal_event", lambda event: recorded.append(event))

    event = Event(type="promoted", source="test", token="token-1")
    signal_id = runner._persist_non_candidate_delivery(event, delivered=False)

    assert signal_id is None
    assert recorded == []


def test_persist_non_candidate_delivery_records_on_success(monkeypatch):
    recorded: list[Event] = []

    monkeypatch.setattr(runner, "record_signal_event", lambda event: (recorded.append(event), "sig-1")[1])

    event = Event(type="heating_up", source="test", token="token-2")
    signal_id = runner._persist_non_candidate_delivery(event, delivered=True)

    assert signal_id == "sig-1"
    assert recorded == [event]
    assert event.extra["_signal_id"] == "sig-1"


def test_persist_candidate_delivery_create_updates_state(monkeypatch):
    recorded: list[tuple[Event, str, bool]] = []
    updated_ids: list[tuple[str, str]] = []
    marked: list[str] = []

    def _record(event, external_ref="", edited=False):
        recorded.append((event, external_ref, edited))

    monkeypatch.setattr(runner, "record_signal_event", _record)
    monkeypatch.setattr(
        "app.services.state_service.update_candidate_message_id",
        lambda token, message_id: updated_ids.append((token, message_id)),
    )
    monkeypatch.setattr(
        "app.services.state_service.mark_candidate_alert_sent",
        lambda token: marked.append(token),
    )

    event = Event(type="candidate", source="test", token="token-3")
    runner._persist_candidate_delivery(
        event,
        delivered=True,
        message_id="msg-123",
        edited=False,
    )

    assert recorded == [(event, "msg-123", False)]
    assert updated_ids == [("token-3", "msg-123")]
    assert marked == ["token-3"]


def test_persist_candidate_delivery_edit_does_not_rewrite_state(monkeypatch):
    recorded: list[tuple[Event, str, bool]] = []

    def _record(event, external_ref="", edited=False):
        recorded.append((event, external_ref, edited))

    monkeypatch.setattr(runner, "record_signal_event", _record)
    monkeypatch.setattr(
        "app.services.state_service.update_candidate_message_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not update message id on edit")),
    )
    monkeypatch.setattr(
        "app.services.state_service.mark_candidate_alert_sent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not mark alert sent on edit")),
    )

    event = Event(type="candidate", source="test", token="token-4")
    runner._persist_candidate_delivery(
        event,
        delivered=True,
        message_id="msg-existing",
        edited=True,
    )

    assert recorded == [(event, "msg-existing", True)]


def test_persist_candidate_delivery_skips_when_not_delivered(monkeypatch):
    recorded: list[tuple[Event, str, bool]] = []

    def _record(event, external_ref="", edited=False):
        recorded.append((event, external_ref, edited))

    monkeypatch.setattr(runner, "record_signal_event", _record)

    event = Event(type="candidate", source="test", token="token-5")
    runner._persist_candidate_delivery(
        event,
        delivered=False,
        message_id="msg-ignored",
        edited=False,
    )

    assert recorded == []


def test_should_send_heating_up_uses_route_decision():
    event = Event(
        type="heating_up",
        source="test",
        token="token-6",
        extra={
            "route_decision": {
                "tier": "sniper",
                "confirmations": ["tracked_wallet_flow", "market_support"],
                "blockers": [],
            }
        },
    )

    assert runner._should_send_heating_up(event) is True


def test_should_send_heating_up_blocks_watch_route():
    event = Event(
        type="heating_up",
        source="test",
        token="token-7",
        extra={
            "route_decision": {
                "tier": "watch",
                "confirmations": [],
                "blockers": ["attention<0.45"],
            }
        },
    )

    assert runner._should_send_heating_up(event) is False


def test_non_candidate_cooldown_key_separates_sniper_from_heating():
    sniper = Event(
        type="heating_up",
        source="test",
        token="token-8",
        extra={"route_decision": {"tier": "sniper"}},
    )
    heating = Event(
        type="heating_up",
        source="test",
        token="token-8",
        extra={"route_decision": {"tier": "heating_up"}},
    )
    promoted = Event(type="promoted", source="test", token="token-8")

    sniper_key, _ = runner._non_candidate_cooldown_key(sniper)
    heating_key, _ = runner._non_candidate_cooldown_key(heating)
    promoted_key, _ = runner._non_candidate_cooldown_key(promoted)

    assert sniper_key == "sniper:token-8"
    assert heating_key == "heating_up:token-8"
    assert promoted_key == "promoted:token-8"

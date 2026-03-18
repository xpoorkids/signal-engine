from worker.events import Event
from worker import runner


def test_persist_non_candidate_delivery_skips_when_not_delivered(monkeypatch):
    recorded: list[Event] = []

    monkeypatch.setattr(runner, "record_signal_event", lambda event: recorded.append(event))

    event = Event(type="promoted", source="test", token="token-1")
    runner._persist_non_candidate_delivery(event, delivered=False)

    assert recorded == []


def test_persist_non_candidate_delivery_records_on_success(monkeypatch):
    recorded: list[Event] = []

    monkeypatch.setattr(runner, "record_signal_event", lambda event: recorded.append(event))

    event = Event(type="heating_up", source="test", token="token-2")
    runner._persist_non_candidate_delivery(event, delivered=True)

    assert recorded == [event]


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

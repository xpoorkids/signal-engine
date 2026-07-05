from worker.events import Event
from worker import runner
import asyncio
from worker.promote import _apply_route_precedence


def test_should_send_heating_up_logs_structured_skip(caplog):
    caplog.set_level("INFO")
    event = Event(
        type="heating_up",
        source="test",
        token="token-log-1",
        extra={
            "route_decision": {
                "tier": "watch",
                "confirmations": [],
                "blockers": ["attention<0.45"],
            }
        },
    )

    assert runner._should_send_heating_up(event) is False
    assert "[heating-up-skip]" in caplog.text
    assert "token=token-log-1" in caplog.text
    assert 'blockers=["attention<0.45"]' in caplog.text


def test_persist_non_candidate_delivery_skips_when_not_delivered(monkeypatch):
    recorded: list[Event] = []

    monkeypatch.setattr(runner, "record_signal_event", lambda event: recorded.append(event))

    event = Event(type="promoted", source="test", token="token-1")
    signal_id = runner._persist_non_candidate_delivery(event, delivered=False)

    assert signal_id is None
    assert recorded == []


def test_persist_non_candidate_delivery_logs_structured_skip(monkeypatch, caplog):
    caplog.set_level("WARNING")
    monkeypatch.setattr(runner, "record_signal_event", lambda event: "sig-unused")

    event = Event(type="promoted", source="test", token="token-log-2")
    signal_id = runner._persist_non_candidate_delivery(event, delivered=False)

    assert signal_id is None
    assert "[dispatch-skip-persist]" in caplog.text
    assert "type=promoted" in caplog.text
    assert "token=token-log-2" in caplog.text
    assert "reason=delivery_failed" in caplog.text


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


def test_worker_health_metadata_exposes_discovery_source_health(monkeypatch):
    monkeypatch.setattr(runner, "_QUEUE", None)
    metadata = runner._worker_health_metadata()

    assert "x_signal_enabled" in metadata
    assert "x_bearer_configured" in metadata
    assert "jupiter_api_key_configured" in metadata
    producer = metadata["producer_health"]
    assert "dex_source_health" in producer
    assert "x_signal_health" in producer
    assert "discord_delivery_health" in producer


def test_apply_route_precedence_drops_candidate_when_sniper_exists():
    candidate = Event(type="candidate", source="engine", token="token-prec-1")
    sniper = Event(
        type="heating_up",
        source="engine",
        token="token-prec-1",
        extra={"route_decision": {"tier": "sniper"}},
    )

    resolved = _apply_route_precedence([candidate, sniper])

    assert [event.type for event in resolved] == ["heating_up"]


def test_apply_route_precedence_keeps_only_promoted_when_present():
    candidate = Event(type="candidate", source="engine", token="token-prec-2")
    sniper = Event(
        type="heating_up",
        source="engine",
        token="token-prec-2",
        extra={"route_decision": {"tier": "sniper"}},
    )
    promoted = Event(type="promoted", source="engine", token="token-prec-2")

    resolved = _apply_route_precedence([candidate, sniper, promoted])

    assert [event.type for event in resolved] == ["promoted"]


def test_derived_event_priority_prefers_promoted_then_sniper_then_candidate():
    events = [
        Event(type="candidate", source="engine", token="token-prec-3"),
        Event(type="promoted", source="engine", token="token-prec-3"),
        Event(type="heating_up", source="engine", token="token-prec-3", extra={"route_decision": {"tier": "sniper"}}),
    ]

    ordered = sorted(events, key=runner._derived_event_priority, reverse=True)

    assert [event.type for event in ordered] == ["promoted", "heating_up", "candidate"]


def test_event_loop_only_records_wallet_signal_after_successful_delivery(monkeypatch):
    queue: asyncio.Queue = asyncio.Queue()
    recorded_wallets: list[tuple[str, str, str]] = []
    persisted: list[bool] = []
    
    async def _process_event(*_args, **_kwargs):
        return [Event(type="heating_up", source="engine", token="token-9", extra={"buyer": "wallet-1", "route_decision": {"tier": "sniper"}})]

    async def _run_once():
        task = asyncio.create_task(runner.event_loop(queue))
        await queue.put(Event(type="trade_buy", source="test", token="token-9", signature="sig-1"))
        await queue.join()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(runner, "state_init", lambda: None)
    monkeypatch.setattr(runner, "learning_init", lambda: None)
    monkeypatch.setattr(runner, "is_sig_new", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "process_event", _process_event)
    monkeypatch.setattr(runner, "_should_send_heating_up", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "can_alert", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "send_discord", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runner, "_persist_non_candidate_delivery", lambda *_args, **_kwargs: persisted.append(False))
    monkeypatch.setattr(runner, "record_wallet_signal", lambda buyer, token, event_type: recorded_wallets.append((buyer, token, event_type)))

    asyncio.run(_run_once())

    assert persisted == [False]
    assert recorded_wallets == []


def test_event_loop_opens_shadow_for_successful_candidate_delivery(monkeypatch):
    queue: asyncio.Queue = asyncio.Queue()
    opened: list[str] = []

    async def _process_event(*_args, **_kwargs):
        return [
            Event(
                type="candidate",
                source="engine",
                token="token-shadow-candidate",
                extra={"candidate_send": True},
            )
        ]

    class _Delivery:
        success = True
        message_id = "message-1"

    async def _run_once():
        task = asyncio.create_task(runner.event_loop(queue))
        await queue.put(Event(type="trade_buy", source="test", token="token-shadow-candidate", signature="sig-shadow"))
        await queue.join()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(runner, "state_init", lambda: None)
    monkeypatch.setattr(runner, "learning_init", lambda: None)
    monkeypatch.setattr(runner, "is_sig_new", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "process_event", _process_event)
    monkeypatch.setattr(runner, "can_alert", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "send_candidate_discord", lambda *_args, **_kwargs: _Delivery())
    monkeypatch.setattr(runner, "_persist_candidate_delivery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "maybe_open_shadow_position", lambda event: opened.append(event.type))

    asyncio.run(_run_once())

    assert opened == ["candidate"]

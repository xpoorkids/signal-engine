import asyncio

from app.services.worker_runtime_service import WorkerRuntimeRepository, build_event_identity
from worker import runner
from worker.discord import DeliveryResult
from worker.events import Event


class CountingQueue(asyncio.Queue):
    def __init__(self):
        super().__init__()
        self.task_done_count = 0

    def task_done(self):
        self.task_done_count += 1
        super().task_done()


def _prepare_runner(monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "worker-v2.db"))
    monkeypatch.setattr(runner, "worker_v2_enabled", lambda: True)
    monkeypatch.setattr(runner, "state_init", lambda: None)
    monkeypatch.setattr(runner, "learning_init", lambda: None)
    monkeypatch.setattr(runner, "SIGNAL_ENGINE_WORKER_V2_EVENT_LEASE_SECONDS", 30)
    monkeypatch.setattr(runner, "SIGNAL_ENGINE_WORKER_V2_MAX_EVENT_ATTEMPTS", 2)
    monkeypatch.setattr(runner, "_WORKER_INSTANCE_ID", "test-worker")


async def _run_one(queue, event):
    task = asyncio.create_task(runner.event_loop(queue))
    await queue.put(event)
    await asyncio.wait_for(queue.join(), timeout=2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_v2_event_loop_task_done_once_for_success_duplicate_and_exception(monkeypatch, tmp_path):
    _prepare_runner(monkeypatch, tmp_path)
    outcomes = [
        [Event(type="candidate", source="engine", token="token-a", extra={"candidate_send": False})],
        [Event(type="candidate", source="engine", token="token-a", extra={"candidate_send": False})],
        RuntimeError("boom"),
    ]

    async def process_event(*_args):
        item = outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(runner, "process_event", process_event)

    queue = CountingQueue()
    event = Event(type="trade_buy", source="helius", token="token-a", signature="sig-a")
    asyncio.run(_run_one(queue, event))
    assert queue.task_done_count == 1

    duplicate_queue = CountingQueue()
    asyncio.run(_run_one(duplicate_queue, event))
    assert duplicate_queue.task_done_count == 1

    failing_queue = CountingQueue()
    asyncio.run(_run_one(failing_queue, Event(type="trade_buy", source="helius", token="token-b", signature="sig-b")))
    assert failing_queue.task_done_count == 1


def test_v2_decision_and_outbox_exist_before_discord_and_success_commits(monkeypatch, tmp_path):
    _prepare_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "ENABLE_DISCORD", True)
    monkeypatch.setattr(runner, "DRY_RUN", False)
    monkeypatch.setattr(runner, "DISCORD_WEBHOOK_URL", "configured")
    recorded = []
    opened = []
    event = Event(type="trade_buy", source="helius", token="token-a", signature="sig-a")
    event_id = build_event_identity(event)
    derived = Event(type="promoted", source="engine", token="token-a", extra={"buyer": "wallet-a"})

    async def process_event(*_args):
        return [derived]

    def send_result(de):
        repo = WorkerRuntimeRepository(tmp_path / "worker-v2.db")
        with repo._connect() as conn:
            decision = conn.execute("SELECT * FROM worker_dispatch_decisions WHERE event_id=?", (event_id,)).fetchone()
            outbox = conn.execute("SELECT * FROM worker_delivery_outbox WHERE event_id=?", (event_id,)).fetchone()
        assert decision is not None
        assert decision["disposition"] == "delivery_pending"
        assert outbox is not None
        assert outbox["status"] == "attempting"
        return DeliveryResult(success=True, attempted=True, status_code=204, reason="sent")

    monkeypatch.setattr(runner, "process_event", process_event)
    monkeypatch.setattr(runner, "_should_send_heating_up", lambda *_args: True)
    monkeypatch.setattr(runner, "send_discord_result", send_result)
    monkeypatch.setattr(runner, "record_signal_event", lambda de: recorded.append(de.token) or "legacy-1")
    monkeypatch.setattr(runner, "record_wallet_signal", lambda *_args: None)
    monkeypatch.setattr(runner, "maybe_open_shadow_position", lambda de: opened.append(de.token))

    asyncio.run(_run_one(CountingQueue(), event))

    repo = WorkerRuntimeRepository(tmp_path / "worker-v2.db")
    health = repo.health_summary(worker_v2_enabled=True, worker_instance_id="test-worker")
    with repo._connect() as conn:
        decision = conn.execute("SELECT * FROM worker_dispatch_decisions WHERE event_id=?", (event_id,)).fetchone()
        outbox = conn.execute("SELECT * FROM worker_delivery_outbox WHERE event_id=?", (event_id,)).fetchone()
        cooldown = conn.execute("SELECT * FROM worker_cooldowns WHERE cooldown_key=?", ("promoted:token-a",)).fetchone()
    assert decision["disposition"] == "delivery_sent"
    assert decision["legacy_signal_id"] == "legacy-1"
    assert outbox["status"] == "sent"
    assert cooldown["last_delivered_ts"] is not None
    assert cooldown["reservation_id"] is None
    assert recorded == ["token-a"]
    assert opened == ["token-a"]
    assert health["pending_outbox_count"] == 0


def test_v2_delivery_failure_keeps_decision_and_releases_cooldown(monkeypatch, tmp_path):
    _prepare_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "ENABLE_DISCORD", True)
    monkeypatch.setattr(runner, "DRY_RUN", False)
    monkeypatch.setattr(runner, "DISCORD_WEBHOOK_URL", "configured")
    recorded = []
    event = Event(type="trade_buy", source="helius", token="token-fail", signature="sig-fail")
    event_id = build_event_identity(event)

    async def process_event(*_args):
        return [Event(type="promoted", source="engine", token="token-fail")]

    monkeypatch.setattr(runner, "process_event", process_event)
    monkeypatch.setattr(runner, "send_discord_result", lambda *_args: DeliveryResult(success=False, attempted=True, status_code=500, reason="http_failure", retryable=True))
    monkeypatch.setattr(runner, "record_signal_event", lambda de: recorded.append(de.token) or "legacy-unused")

    asyncio.run(_run_one(CountingQueue(), event))

    repo = WorkerRuntimeRepository(tmp_path / "worker-v2.db")
    with repo._connect() as conn:
        decision = conn.execute("SELECT * FROM worker_dispatch_decisions WHERE event_id=?", (event_id,)).fetchone()
        outbox = conn.execute("SELECT * FROM worker_delivery_outbox WHERE event_id=?", (event_id,)).fetchone()
        cooldown = conn.execute("SELECT * FROM worker_cooldowns WHERE cooldown_key=?", ("promoted:token-fail",)).fetchone()
    assert decision["disposition"] == "delivery_failed"
    assert outbox["status"] == "failed"
    assert outbox["status_code"] == 500
    assert cooldown["reservation_id"] is None
    assert recorded == []


def test_v2_dry_run_is_suppressed_without_delivery_attempt(monkeypatch, tmp_path):
    _prepare_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "ENABLE_DISCORD", True)
    monkeypatch.setattr(runner, "DRY_RUN", True)
    monkeypatch.setattr(runner, "DISCORD_WEBHOOK_URL", "configured")
    event = Event(type="trade_buy", source="helius", token="token-dry", signature="sig-dry")
    event_id = build_event_identity(event)

    async def process_event(*_args):
        return [Event(type="promoted", source="engine", token="token-dry")]

    monkeypatch.setattr(runner, "process_event", process_event)
    monkeypatch.setattr(runner, "send_discord_result", lambda *_args: (_ for _ in ()).throw(AssertionError("discord should not be contacted")))

    asyncio.run(_run_one(CountingQueue(), event))

    repo = WorkerRuntimeRepository(tmp_path / "worker-v2.db")
    with repo._connect() as conn:
        decision = conn.execute("SELECT * FROM worker_dispatch_decisions WHERE event_id=?", (event_id,)).fetchone()
        outbox = conn.execute("SELECT * FROM worker_delivery_outbox WHERE event_id=?", (event_id,)).fetchone()
    assert decision["disposition"] == "dry_run_suppressed"
    assert outbox["status"] == "suppressed"


def test_v2_uncertain_delivery_keeps_reservation_and_does_not_record_legacy(monkeypatch, tmp_path):
    _prepare_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "ENABLE_DISCORD", True)
    monkeypatch.setattr(runner, "DRY_RUN", False)
    monkeypatch.setattr(runner, "DISCORD_WEBHOOK_URL", "configured")
    recorded = []
    event = Event(type="trade_buy", source="helius", token="token-uncertain", signature="sig-uncertain")
    event_id = build_event_identity(event)

    async def process_event(*_args):
        return [Event(type="promoted", source="engine", token="token-uncertain")]

    monkeypatch.setattr(runner, "process_event", process_event)
    monkeypatch.setattr(
        runner,
        "send_discord_result",
        lambda *_args: DeliveryResult(success=False, attempted=True, reason="timeout", error_type="Timeout", ambiguous=True),
    )
    monkeypatch.setattr(runner, "record_signal_event", lambda de: recorded.append(de.token) or "legacy-unused")

    asyncio.run(_run_one(CountingQueue(), event))

    repo = WorkerRuntimeRepository(tmp_path / "worker-v2.db")
    with repo._connect() as conn:
        decision = conn.execute("SELECT * FROM worker_dispatch_decisions WHERE event_id=?", (event_id,)).fetchone()
        outbox = conn.execute("SELECT * FROM worker_delivery_outbox WHERE event_id=?", (event_id,)).fetchone()
        cooldown = conn.execute("SELECT * FROM worker_cooldowns WHERE cooldown_key=?", ("promoted:token-uncertain",)).fetchone()
    assert decision["disposition"] == "delivery_uncertain"
    assert outbox["status"] == "delivery_uncertain"
    assert cooldown["reservation_id"] == decision["decision_id"]
    assert recorded == []


def test_v2_candidate_success_updates_message_state_after_delivery(monkeypatch, tmp_path):
    _prepare_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "ENABLE_DISCORD", True)
    monkeypatch.setattr(runner, "DRY_RUN", False)
    monkeypatch.setattr(runner, "DISCORD_CANDIDATE_WEBHOOK", "configured")
    persisted = []
    event = Event(type="trade_buy", source="helius", token="token-cand", signature="sig-cand")
    event_id = build_event_identity(event)

    async def process_event(*_args):
        return [Event(type="candidate", source="engine", token="token-cand", extra={"candidate_send": True})]

    monkeypatch.setattr(runner, "process_event", process_event)
    monkeypatch.setattr(runner, "send_candidate_discord", lambda *_args, **_kwargs: DeliveryResult(success=True, attempted=True, message_id="msg-1", status_code=200, reason="sent"))
    monkeypatch.setattr(runner, "_persist_candidate_delivery", lambda *args, **kwargs: persisted.append(kwargs))
    monkeypatch.setattr(runner, "maybe_open_shadow_position", lambda *_args: None)

    asyncio.run(_run_one(CountingQueue(), event))

    repo = WorkerRuntimeRepository(tmp_path / "worker-v2.db")
    with repo._connect() as conn:
        decision = conn.execute("SELECT * FROM worker_dispatch_decisions WHERE event_id=?", (event_id,)).fetchone()
        outbox = conn.execute("SELECT * FROM worker_delivery_outbox WHERE event_id=?", (event_id,)).fetchone()
    assert decision["disposition"] == "delivery_sent"
    assert outbox["status"] == "sent"
    assert persisted[0]["delivered"] is True
    assert persisted[0]["message_id"] == "msg-1"

import asyncio

import pytest

from worker import runner


async def _never():
    await asyncio.Event().wait()


def test_critical_task_failure_propagates_and_cancels_other_tasks():
    async def failing():
        raise RuntimeError("critical failed")

    async def run():
        task_a = asyncio.create_task(failing(), name="event_loop")
        task_b = asyncio.create_task(_never(), name="heartbeat_loop")
        with pytest.raises(runner.WorkerTaskFailure):
            await runner._run_worker_v2_supervised([task_a, task_b], {"event_loop", "heartbeat_loop"})
        assert task_b.cancelled()

    asyncio.run(run())


def test_critical_task_unexpected_return_propagates():
    async def returns():
        return None

    async def run():
        task = asyncio.create_task(returns(), name="event_loop")
        with pytest.raises(runner.WorkerTaskFailure):
            await runner._run_worker_v2_supervised([task], {"event_loop"})

    asyncio.run(run())


def test_optional_task_restarts_with_bounded_backoff(monkeypatch):
    attempts = {"count": 0}
    real_sleep = asyncio.sleep

    async def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary")
        await asyncio.Event().wait()

    async def no_sleep(_seconds):
        await real_sleep(0)

    async def run():
        monkeypatch.setattr(runner.asyncio, "sleep", no_sleep)
        monkeypatch.setattr(runner.random, "uniform", lambda *_args: 0)
        task = asyncio.create_task(runner._optional_task_supervisor("flaky", flaky, max_restarts=3, base_delay=0.01))
        while attempts["count"] < 3:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert runner._OPTIONAL_TASK_RESTART_COUNTS["flaky"] == 2


def test_optional_task_exceeds_restart_budget():
    async def failing():
        raise RuntimeError("always")

    async def run():
        with pytest.raises(runner.WorkerTaskFailure):
            await runner._optional_task_supervisor("always", failing, max_restarts=1, base_delay=0)

    asyncio.run(run())


def test_run_worker_v2_storage_failure_exits_before_tasks(monkeypatch, tmp_path):
    created = []

    monkeypatch.setattr(runner, "worker_v2_enabled", lambda: True)
    monkeypatch.setattr(runner, "resolve_engine_db_path", lambda: tmp_path / "missing.db")
    monkeypatch.setattr(runner, "_storage_write_available", lambda _path: False)
    monkeypatch.setattr(runner, "_create_worker_task", lambda name, awaitable: created.append(name))

    with pytest.raises(RuntimeError, match="worker_v2_storage_unavailable"):
        asyncio.run(runner.run_worker())
    assert created == []


def test_main_exits_nonzero_in_worker_v2(monkeypatch):
    async def failing_run_worker():
        raise RuntimeError("fatal")

    monkeypatch.setattr(runner, "worker_v2_enabled", lambda: True)
    monkeypatch.setattr(runner, "run_worker", failing_run_worker)

    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 1

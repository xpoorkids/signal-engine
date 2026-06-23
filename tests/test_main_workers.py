from app import main


def _clear_worker_env(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("SIGNAL_ENGINE_ENABLE_BACKGROUND_WORKERS", raising=False)
    monkeypatch.delenv("SIGNAL_ENGINE_ENABLE_LEARNING_WORKERS", raising=False)
    monkeypatch.delenv("SIGNAL_ENGINE_ENABLE_SNAPSHOT_WORKER", raising=False)


def test_snapshot_worker_stays_enabled_when_legacy_learning_workers_are_disabled(monkeypatch):
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("SIGNAL_ENGINE_ENABLE_LEARNING_WORKERS", "false")

    assert main._snapshot_worker_enabled() is True
    assert main._learning_workers_enabled() is False


def test_snapshot_worker_has_dedicated_disable_override(monkeypatch):
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("SIGNAL_ENGINE_ENABLE_SNAPSHOT_WORKER", "false")

    assert main._snapshot_worker_enabled() is False


def test_global_background_worker_switch_disables_snapshot_worker(monkeypatch):
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("SIGNAL_ENGINE_ENABLE_BACKGROUND_WORKERS", "false")

    assert main._snapshot_worker_enabled() is False

from __future__ import annotations

import importlib
import sys


def _reload_worker_config():
    sys.modules.pop("worker.config", None)
    return importlib.import_module("worker.config")


def test_execution_mode_defaults_to_shadow_from_legacy_flags(monkeypatch):
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    monkeypatch.setenv("ENABLE_SHADOW_EXECUTION", "1")
    monkeypatch.setenv("ENABLE_PRETRADE_VALIDATION", "1")

    config = _reload_worker_config()

    assert config.EXECUTION_MODE == "shadow"
    assert config.TRADE_VALIDATION_ENABLED is True
    assert config.SHADOW_EXECUTION_ENABLED is True
    assert config.LIVE_EXECUTION_REQUESTED is False


def test_execution_mode_validate_only_disables_shadow(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "validate_only")

    config = _reload_worker_config()

    assert config.EXECUTION_MODE == "validate_only"
    assert config.TRADE_VALIDATION_ENABLED is True
    assert config.SHADOW_EXECUTION_ENABLED is False
    assert config.LIVE_EXECUTION_REQUESTED is False


def test_execution_mode_live_is_fail_closed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")

    config = _reload_worker_config()

    assert config.EXECUTION_MODE == "live"
    assert config.TRADE_VALIDATION_ENABLED is True
    assert config.SHADOW_EXECUTION_ENABLED is False
    assert config.LIVE_EXECUTION_REQUESTED is True

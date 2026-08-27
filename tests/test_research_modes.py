from __future__ import annotations

from research.cli import main
from research.modes import ResearchModeError, ensure_mode_allows_fixture, ensure_mode_allows_source, resolve_mode


def test_mutating_command_requires_explicit_mode(monkeypatch) -> None:
    monkeypatch.delenv("SIGNAL_ENGINE_RESEARCH_MODE", raising=False)
    try:
        resolve_mode(None, command="backfill")
    except ResearchModeError as exc:
        assert "research_mode_required" in str(exc)
    else:
        raise AssertionError("expected mode error")


def test_source_mode_cannot_use_fixture_builders() -> None:
    try:
        ensure_mode_allows_fixture("source")
    except ResearchModeError as exc:
        assert "source_mode_cannot_use_fixture" in str(exc)
    else:
        raise AssertionError("expected source fixture error")


def test_fixture_mode_cannot_call_source_adapters() -> None:
    try:
        ensure_mode_allows_source("fixture")
    except ResearchModeError as exc:
        assert "fixture_mode_cannot_call_source" in str(exc)
    else:
        raise AssertionError("expected fixture source error")


def test_source_cli_does_not_call_fixture_backfill(tmp_path, monkeypatch, capsys) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("fixture backfill called")

    monkeypatch.setenv("SIGNAL_ENGINE_RESEARCH_DB_PATH", str(tmp_path / "research.db"))
    monkeypatch.setenv("SIGNAL_ENGINE_RESEARCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIGNAL_ENGINE_RESEARCH_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr("research.cli.run_fixture_backfill", boom)
    code = main(["backfill", "--mode", "source", "--source", "helius", "--token", "FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2"])
    assert code == 0
    assert '"data_mode": "source"' in capsys.readouterr().out


def test_fixture_cli_does_not_call_network_sources(tmp_path, monkeypatch, capsys) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("source backfill called")

    monkeypatch.setenv("SIGNAL_ENGINE_RESEARCH_DB_PATH", str(tmp_path / "research.db"))
    monkeypatch.setenv("SIGNAL_ENGINE_RESEARCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIGNAL_ENGINE_RESEARCH_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr("research.cli.run_source_backfill", boom)
    code = main(["validate-seeds", "--mode", "fixture"])
    assert code == 0
    assert '"data_mode": "fixture"' in capsys.readouterr().out


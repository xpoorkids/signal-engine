from __future__ import annotations

import os

from research.models import RESEARCH_MODES


MUTATING_COMMANDS = {
    "validate-seeds",
    "backfill",
    "build-features",
    "build-outcomes",
    "build-controls",
    "replay-actions",
    "report",
}


class ResearchModeError(ValueError):
    pass


def resolve_mode(cli_mode: str | None, *, command: str | None = None, require_explicit_for_mutation: bool = True) -> str:
    env_mode = os.getenv("SIGNAL_ENGINE_RESEARCH_MODE")
    mode = (cli_mode or env_mode or "fixture").strip().lower()
    if mode not in RESEARCH_MODES:
        raise ResearchModeError(f"invalid_research_mode:{mode}")
    if require_explicit_for_mutation and command in MUTATING_COMMANDS and not cli_mode and not env_mode:
        raise ResearchModeError("research_mode_required_for_mutating_command")
    return mode


def ensure_mode_allows_fixture(mode: str) -> None:
    if mode == "source":
        raise ResearchModeError("source_mode_cannot_use_fixture_builders")


def ensure_mode_allows_source(mode: str) -> None:
    if mode == "fixture":
        raise ResearchModeError("fixture_mode_cannot_call_source_adapters")


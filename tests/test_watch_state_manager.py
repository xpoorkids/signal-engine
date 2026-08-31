from app.watch import watch_state_manager
from app.watch.stages import StageDecision


def test_evolve_watch_stage_dry_run_has_no_persistence_side_effects(monkeypatch):
    persisted = []
    monkeypatch.setattr(
        watch_state_manager,
        "classify_watch_stage",
        lambda signals: StageDecision("building", 7, ["test"], signals),
    )
    monkeypatch.setattr(
        watch_state_manager,
        "persist_stage_state",
        lambda *args: persisted.append(args),
    )
    monkeypatch.setattr(watch_state_manager, "_STATE", {})

    decision = watch_state_manager.evolve_watch_stage(
        {"token": "test-token", "chain": "solana"},
        dry_run=True,
    )

    assert decision.stage == "building"
    assert persisted == []
    assert watch_state_manager._STATE == {}


def test_evolve_watch_stage_persists_by_default(monkeypatch):
    persisted = []
    monkeypatch.setattr(
        watch_state_manager,
        "classify_watch_stage",
        lambda signals: StageDecision("building", 7, ["test"], signals),
    )
    monkeypatch.setattr(
        watch_state_manager,
        "persist_stage_state",
        lambda *args: persisted.append(args),
    )
    monkeypatch.setattr(watch_state_manager, "_STATE", {})

    watch_state_manager.evolve_watch_stage({"token": "test-token", "chain": "solana"})

    assert persisted == [("test-token", "solana", "building", 7, ["test"])]
    assert watch_state_manager._STATE["test-token"]["stage"] == "building"

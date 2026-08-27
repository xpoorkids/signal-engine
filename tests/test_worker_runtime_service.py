import concurrent.futures
import sqlite3
import time

from app.services.worker_runtime_service import WorkerRuntimeRepository, build_event_identity, sanitize_error_message
from worker.events import Event


def _repo(tmp_path):
    repo = WorkerRuntimeRepository(tmp_path / "worker-runtime.db")
    repo.init_schema()
    return repo


def test_event_identity_signature_is_stable_and_ignores_generated_event_id():
    first = Event(type="trade_buy", source="helius", token="token-a", signature="sig-a", extra={"instruction_index": 2})
    second = Event(type="trade_buy", source="helius", token="token-a", signature="sig-a", extra={"instruction_index": 2})
    second.id = "different-generated-id"

    assert build_event_identity(first) == build_event_identity(second)
    assert build_event_identity(first) != build_event_identity(Event(type="trade_buy", source="helius", token="token-a", signature="sig-b"))
    assert build_event_identity(first) != build_event_identity(Event(type="trade_buy", source="helius", token="token-a", signature="sig-a", extra={"instruction_index": 3}))


def test_event_identity_canonical_json_and_source_event_id():
    a = Event(type="manual_review", source="manual", token="token-a", extra={"source_event_id": "review-1", "b": 2, "a": 1})
    b = Event(type="manual_review", source="manual", token="token-a", extra={"a": 1, "b": 2, "source_event_id": "review-1"})

    assert build_event_identity(a) == build_event_identity(b)
    assert build_event_identity(a) != build_event_identity(Event(type="manual_review", source="manual", token="token-a", extra={"source_event_id": "review-2"}))


def test_dex_scan_and_recheck_identity_windows_are_stable():
    dex_a = Event(type="token_resolved", source="dex_scan", token="token-a", extra={"scan_started_ts": 111})
    dex_b = Event(type="token_resolved", source="dex_scan", token="token-a", extra={"scan_started_ts": 111})
    dex_c = Event(type="token_resolved", source="dex_scan", token="token-a", extra={"scan_started_ts": 222})
    recheck_a = Event(type="recheck", source="observe_recheck", token="token-a", extra={"stage": "candidate", "scheduled_ts": 333})
    recheck_b = Event(type="recheck", source="observe_recheck", token="token-a", extra={"stage": "candidate", "scheduled_ts": 333})

    assert build_event_identity(dex_a) == build_event_identity(dex_b)
    assert build_event_identity(dex_a) != build_event_identity(dex_c)
    assert build_event_identity(recheck_a) == build_event_identity(recheck_b)


def test_event_claim_duplicate_reinit_lease_reclaim_and_dead_letter(tmp_path):
    repo = _repo(tmp_path)
    event = Event(type="trade_buy", source="helius", token="token-a", signature="sig-a")

    claim = repo.claim_event(event, worker_id="worker-a", lease_seconds=1, max_attempts=2, now_ts=100)
    assert claim.claimed is True
    assert claim.attempt_count == 1

    blocked = repo.claim_event(event, worker_id="worker-b", lease_seconds=1, max_attempts=2, now_ts=100)
    assert blocked.active_lease is True

    reclaimed = repo.claim_event(event, worker_id="worker-b", lease_seconds=1, max_attempts=2, now_ts=102)
    assert reclaimed.claimed is True
    assert reclaimed.reclaimed is True
    assert reclaimed.attempt_count == 2

    failed = repo.fail_event(reclaimed.event_id, error=RuntimeError("bad api_key=secret"), max_attempts=2)
    assert failed.status == "dead_letter"
    dead = repo.list_recent_dead_letters()
    assert len(dead) == 1
    assert "secret" not in dead[0]["error_message"]
    assert "api_key=[redacted]" in dead[0]["error_message"]

    duplicate = WorkerRuntimeRepository(tmp_path / "worker-runtime.db").claim_event(event, worker_id="worker-c", max_attempts=2)
    assert duplicate.status == "dead_letter"
    assert duplicate.claimed is False


def test_completed_event_duplicate_survives_new_repository(tmp_path):
    repo = _repo(tmp_path)
    event = Event(type="trade_buy", source="helius", token="token-a", signature="sig-a")
    claim = repo.claim_event(event, worker_id="worker-a")
    repo.complete_event(claim.event_id)

    duplicate = WorkerRuntimeRepository(tmp_path / "worker-runtime.db").claim_event(event, worker_id="worker-b")
    assert duplicate.duplicate is True
    assert duplicate.reason == "completed_duplicate"


def test_two_concurrent_claim_attempts_produce_one_owner(tmp_path):
    repo = _repo(tmp_path)
    event = Event(type="trade_buy", source="helius", token="token-a", signature="sig-a")

    def claim(worker_id):
        return WorkerRuntimeRepository(tmp_path / "worker-runtime.db").claim_event(event, worker_id=worker_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["worker-a", "worker-b"]))

    assert sum(1 for item in results if item.claimed) == 1
    assert sum(1 for item in results if item.active_lease) == 1


def test_cooldown_reservation_commit_release_and_reinit(tmp_path):
    repo = _repo(tmp_path)

    first = repo.reserve_cooldown("promoted:token-a", 60, "decision-a", now_ts=100)
    assert first.allowed is True
    assert repo.commit_cooldown("promoted:token-a", "wrong") is False
    assert repo.commit_cooldown("promoted:token-a", "decision-a", delivered_ts=101) is True

    reloaded = WorkerRuntimeRepository(tmp_path / "worker-runtime.db")
    second = reloaded.reserve_cooldown("promoted:token-a", 60, "decision-b", now_ts=120)
    assert second.allowed is False
    assert second.reason == "cooldown_active"

    candidate = reloaded.reserve_cooldown("candidate:token-a", 60, "decision-c", now_ts=120)
    assert candidate.allowed is True
    assert reloaded.release_cooldown("candidate:token-a", "wrong") is False
    assert reloaded.release_cooldown("candidate:token-a", "decision-c", reason="http_failure") is True
    assert reloaded.reserve_cooldown("candidate:token-a", 60, "decision-d", now_ts=121).allowed is True


def test_expired_reservation_can_be_replaced(tmp_path):
    repo = _repo(tmp_path)
    assert repo.reserve_cooldown("heating_up:token-a", 10, "decision-a", now_ts=100).allowed is True
    assert repo.reserve_cooldown("heating_up:token-a", 10, "decision-b", now_ts=101).reason == "active_reservation"
    assert repo.reserve_cooldown("heating_up:token-a", 10, "decision-b", now_ts=500).allowed is True


def test_checkpoints_are_monotonic_and_survive_reinit(tmp_path):
    repo = _repo(tmp_path)
    assert repo.advance_checkpoint("source:helius:completed", source="helius", stage="completed", slot=10, signature="sig-10", event_id="event-10", observed_ts=1.0)
    assert repo.advance_checkpoint("source:helius:completed", source="helius", stage="completed", slot=9, signature="sig-9", event_id="event-9", observed_ts=2.0) is False
    assert repo.advance_checkpoint("source:helius:completed", source="helius", stage="completed", slot=11, signature="sig-11", event_id="event-11", observed_ts=3.0)
    assert repo.advance_checkpoint("source:dex_scan:completed", source="dex_scan", stage="completed", slot=None, signature=None, event_id="event-dex", observed_ts=4.0)

    health = WorkerRuntimeRepository(tmp_path / "worker-runtime.db").health_summary(worker_v2_enabled=True, worker_instance_id="worker-a")
    checkpoints = {item["checkpoint_key"]: item for item in health["latest_checkpoints_by_source"]}
    assert checkpoints["source:helius:completed"]["slot"] == 11
    assert checkpoints["source:dex_scan:completed"]["event_id"] == "event-dex"


def test_dead_letter_review_and_replayable_state(tmp_path):
    repo = _repo(tmp_path)
    event = Event(type="trade_buy", source="helius", token="token-a", signature="sig-a")
    claim = repo.claim_event(event, worker_id="worker-a", max_attempts=1)
    repo.fail_event(claim.event_id, error=RuntimeError("failed"), max_attempts=1)
    dead = repo.list_recent_dead_letters()[0]

    assert repo.mark_dead_letter_reviewed(dead["dead_letter_id"]) is True
    assert repo.get_dead_letter(dead["dead_letter_id"])["replay_status"] == "reviewed"
    assert repo.reset_dead_letter_to_replayable(dead["dead_letter_id"]) is True
    assert repo.get_dead_letter(dead["dead_letter_id"])["replay_status"] == "replayable"


def test_schema_tables_and_indexes_exist(tmp_path):
    repo = _repo(tmp_path)
    with sqlite3.connect(tmp_path / "worker-runtime.db") as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    assert {
        "worker_events",
        "worker_dispatch_decisions",
        "worker_delivery_outbox",
        "worker_cooldowns",
        "worker_checkpoints",
        "worker_dead_letters",
        "idx_worker_events_status",
        "idx_worker_events_source_slot",
        "idx_worker_outbox_status",
        "idx_worker_cooldowns_reservation",
    } <= names

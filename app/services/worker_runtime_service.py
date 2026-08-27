from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.db_service import connect_sqlite, resolve_engine_db_path
from worker.events import Event, as_dict


EVENT_IDENTITY_VERSION = "worker-event-id-v1"
EVENT_STATUSES = {"received", "processing", "completed", "failed", "dead_letter"}
DECISION_DISPOSITIONS = {
    "derived",
    "delivery_eligible",
    "cooldown_suppressed",
    "quality_suppressed",
    "candidate_send_suppressed",
    "dry_run_suppressed",
    "delivery_disabled",
    "delivery_pending",
    "delivery_sent",
    "delivery_failed",
    "delivery_uncertain",
}
OUTBOX_STATUSES = {"pending", "attempting", "sent", "suppressed", "failed", "delivery_uncertain", "dead_letter"}


@dataclass(frozen=True)
class EventClaim:
    event_id: str
    claimed: bool
    duplicate: bool
    active_lease: bool
    reclaimed: bool
    attempt_count: int
    status: str
    reason: str


@dataclass(frozen=True)
class CooldownDecision:
    cooldown_key: str
    allowed: bool
    reserved: bool
    reason: str
    last_delivered_ts: int | None = None
    reservation_id: str | None = None
    reservation_expires_ts: int | None = None


def worker_v2_enabled() -> bool:
    return os.getenv("SIGNAL_ENGINE_WORKER_V2_ENABLED", "0").strip().lower() in {"1", "true", "yes", "y", "on"}


def worker_v2_max_event_attempts() -> int:
    raw = os.getenv("SIGNAL_ENGINE_WORKER_V2_MAX_EVENT_ATTEMPTS", "3").strip()
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 3


def worker_v2_event_lease_seconds() -> int:
    raw = os.getenv("SIGNAL_ENGINE_WORKER_V2_EVENT_LEASE_SECONDS", "120").strip()
    try:
        return max(5, int(raw))
    except (TypeError, ValueError):
        return 120


def build_worker_instance_id() -> str:
    configured = os.getenv("SIGNAL_ENGINE_WORKER_NAME", "").strip()
    if configured:
        return configured
    service = os.getenv("RENDER_SERVICE_NAME", "").strip()
    instance = os.getenv("RENDER_INSTANCE_ID", "").strip()
    if service or instance:
        return ":".join(part for part in (service, instance) if part)
    return f"worker-{uuid.uuid4().hex}"


def _json_default(value: Any) -> str:
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def _safe_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    secret_markers = ("key", "secret", "token", "webhook", "authorization", "password", "phrase")
    safe: dict[str, Any] = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in secret_markers):
            safe[str(key)] = "[redacted]"
        elif isinstance(item, dict):
            safe[str(key)] = _safe_payload(item)
        elif isinstance(item, list):
            safe[str(key)] = [_safe_payload(v) if isinstance(v, dict) else v for v in item]
        else:
            safe[str(key)] = item
    return safe


def sanitize_error_message(error: BaseException | str | None) -> str:
    text = str(error or "")
    text = re.sub(r"https://discord(?:app)?\.com/api/webhooks/[^\s]+", "[redacted-discord-webhook]", text)
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer|token|secret)=?[^\s,;]+", r"\1=[redacted]", text)
    return text[:1000]


def _identity_from_extra(extra: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = extra.get(key)
        if value not in (None, "", []):
            return str(value)
    return None


def _stable_payload_fields(event: Event) -> dict[str, Any]:
    extra = event.extra if isinstance(event.extra, dict) else {}
    allowed_extra_keys = (
        "stage",
        "scheduled_ts",
        "scan_window",
        "scan_started_ts",
        "request_id",
        "assessment_id",
        "source_event_id",
        "instruction_index",
    )
    return {
        "type": event.type,
        "source": event.source,
        "token": event.token,
        "signature": event.signature,
        "slot": event.slot,
        "program": event.program,
        "creator": event.creator,
        "reasons": sorted(str(r) for r in (event.reasons or [])),
        "extra": {key: extra.get(key) for key in allowed_extra_keys if key in extra},
    }


def build_event_identity(event: Event) -> str:
    extra = event.extra if isinstance(event.extra, dict) else {}
    source = str(event.source or "unknown")
    event_type = str(event.type or "unknown")
    token = str(event.token or "")
    if event.signature:
        instruction_index = _identity_from_extra(extra, ("instruction_index", "instruction_idx", "ix_index", "inner_instruction_index"))
        parts = [EVENT_IDENTITY_VERSION, source, str(event.signature), event_type, token]
        if instruction_index is not None:
            parts.append(f"ix={instruction_index}")
        identity = "|".join(parts)
    else:
        source_event_id = _identity_from_extra(extra, ("source_event_id", "source_id", "event_id", "request_id", "assessment_id"))
        if source_event_id:
            identity = "|".join([EVENT_IDENTITY_VERSION, source, event_type, token, f"source_id={source_event_id}"])
        elif source == "dex_scan":
            window = _identity_from_extra(extra, ("scan_window", "scan_started_ts"))
            metrics = extra.get("metrics") if isinstance(extra.get("metrics"), dict) else {}
            if window is None:
                dex_health = metrics.get("dex_source_health") if isinstance(metrics.get("dex_source_health"), dict) else {}
                window = dex_health.get("last_started_ts")
            identity = "|".join([EVENT_IDENTITY_VERSION, f"dex_scan:{token}:{window or int((event.ts or time.time()) // 300)}"])
        elif event_type == "recheck" or source.endswith("recheck"):
            stage = str(extra.get("stage") or extra.get("recheck_stage") or "unknown")
            scheduled = str(extra.get("scheduled_ts") or extra.get("scheduled_timestamp") or int((event.ts or time.time()) // 60) * 60)
            identity = "|".join([EVENT_IDENTITY_VERSION, f"recheck:{token}:{stage}:{scheduled}"])
        elif event.slot is not None:
            identity = "|".join([EVENT_IDENTITY_VERSION, source, f"slot={event.slot}", event_type, token])
        else:
            bucket = int((event.ts or time.time()) // 60) * 60
            identity = "|".join([EVENT_IDENTITY_VERSION, source, event_type, token, f"bucket={bucket}", _canonical_json(_stable_payload_fields(event))])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _event_payload_json(event: Event) -> str:
    payload = as_dict(event)
    payload.pop("id", None)
    payload["extra"] = _safe_payload(payload.get("extra") if isinstance(payload.get("extra"), dict) else {})
    return _canonical_json(payload)


class WorkerRuntimeRepository:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else resolve_engine_db_path()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.db_path)

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS worker_events (
                    event_id TEXT PRIMARY KEY,
                    event_version TEXT,
                    source TEXT,
                    event_type TEXT,
                    token TEXT,
                    signature TEXT,
                    slot INTEGER,
                    observed_ts REAL,
                    received_ts INTEGER,
                    payload_json TEXT,
                    status TEXT,
                    attempt_count INTEGER DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_ts INTEGER,
                    first_attempt_ts INTEGER,
                    last_attempt_ts INTEGER,
                    completed_ts INTEGER,
                    last_error_type TEXT,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_worker_events_status ON worker_events(status);
                CREATE INDEX IF NOT EXISTS idx_worker_events_source_slot ON worker_events(source, slot);
                CREATE INDEX IF NOT EXISTS idx_worker_events_token ON worker_events(token);
                CREATE INDEX IF NOT EXISTS idx_worker_events_signature ON worker_events(signature);
                CREATE INDEX IF NOT EXISTS idx_worker_events_lease ON worker_events(status, lease_expires_ts);
                CREATE INDEX IF NOT EXISTS idx_worker_events_received ON worker_events(received_ts);

                CREATE TABLE IF NOT EXISTS worker_dispatch_decisions (
                    decision_id TEXT PRIMARY KEY,
                    event_id TEXT,
                    derived_event_type TEXT,
                    token TEXT,
                    route_tier TEXT,
                    disposition TEXT,
                    reason TEXT,
                    payload_json TEXT,
                    created_ts INTEGER,
                    updated_ts INTEGER,
                    delivered_ts INTEGER,
                    delivery_id TEXT,
                    legacy_signal_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_worker_decisions_event ON worker_dispatch_decisions(event_id);
                CREATE INDEX IF NOT EXISTS idx_worker_decisions_token ON worker_dispatch_decisions(token);
                CREATE INDEX IF NOT EXISTS idx_worker_decisions_disposition ON worker_dispatch_decisions(disposition);
                CREATE INDEX IF NOT EXISTS idx_worker_decisions_created ON worker_dispatch_decisions(created_ts);

                CREATE TABLE IF NOT EXISTS worker_delivery_outbox (
                    delivery_id TEXT PRIMARY KEY,
                    decision_id TEXT,
                    event_id TEXT,
                    channel TEXT,
                    operation TEXT,
                    destination_key TEXT,
                    token TEXT,
                    event_type TEXT,
                    payload_json TEXT,
                    edit_message_id TEXT,
                    status TEXT,
                    attempt_count INTEGER DEFAULT 0,
                    created_ts INTEGER,
                    attempt_started_ts INTEGER,
                    updated_ts INTEGER,
                    delivered_ts INTEGER,
                    external_message_id TEXT,
                    status_code INTEGER,
                    failure_reason TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    retryable INTEGER,
                    next_attempt_ts INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_worker_outbox_status ON worker_delivery_outbox(status);
                CREATE INDEX IF NOT EXISTS idx_worker_outbox_decision ON worker_delivery_outbox(decision_id);
                CREATE INDEX IF NOT EXISTS idx_worker_outbox_event ON worker_delivery_outbox(event_id);
                CREATE INDEX IF NOT EXISTS idx_worker_outbox_token ON worker_delivery_outbox(token);
                CREATE INDEX IF NOT EXISTS idx_worker_outbox_next_attempt ON worker_delivery_outbox(status, next_attempt_ts);

                CREATE TABLE IF NOT EXISTS worker_cooldowns (
                    cooldown_key TEXT PRIMARY KEY,
                    last_delivered_ts INTEGER,
                    reservation_id TEXT,
                    reservation_started_ts INTEGER,
                    reservation_expires_ts INTEGER,
                    updated_ts INTEGER,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_worker_cooldowns_reservation ON worker_cooldowns(reservation_expires_ts);
                CREATE INDEX IF NOT EXISTS idx_worker_cooldowns_delivered ON worker_cooldowns(last_delivered_ts);

                CREATE TABLE IF NOT EXISTS worker_checkpoints (
                    checkpoint_key TEXT PRIMARY KEY,
                    source TEXT,
                    stage TEXT,
                    slot INTEGER,
                    signature TEXT,
                    event_id TEXT,
                    observed_ts REAL,
                    updated_ts INTEGER,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_worker_checkpoints_source ON worker_checkpoints(source, stage);
                CREATE INDEX IF NOT EXISTS idx_worker_checkpoints_updated ON worker_checkpoints(updated_ts);

                CREATE TABLE IF NOT EXISTS worker_dead_letters (
                    dead_letter_id TEXT PRIMARY KEY,
                    object_type TEXT,
                    object_id TEXT,
                    event_id TEXT,
                    failure_stage TEXT,
                    attempt_count INTEGER,
                    payload_json TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    created_ts INTEGER,
                    last_failed_ts INTEGER,
                    replay_status TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_dead_letters_object ON worker_dead_letters(object_type, object_id);
                CREATE INDEX IF NOT EXISTS idx_worker_dead_letters_event ON worker_dead_letters(event_id);
                CREATE INDEX IF NOT EXISTS idx_worker_dead_letters_created ON worker_dead_letters(created_ts);
                CREATE INDEX IF NOT EXISTS idx_worker_dead_letters_replay ON worker_dead_letters(replay_status);
                """
            )

    def claim_event(
        self,
        event: Event,
        *,
        worker_id: str,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
        now_ts: int | None = None,
    ) -> EventClaim:
        event_id = build_event_identity(event)
        now = int(now_ts or time.time())
        lease_seconds = worker_v2_event_lease_seconds() if lease_seconds is None else max(1, int(lease_seconds))
        max_attempts = worker_v2_max_event_attempts() if max_attempts is None else max(1, int(max_attempts))
        payload_json = _event_payload_json(event)
        lease_expires = now + lease_seconds
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR IGNORE INTO worker_events (
                    event_id, event_version, source, event_type, token, signature, slot,
                    observed_ts, received_ts, payload_json, status, attempt_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', 0)
                """,
                (
                    event_id,
                    EVENT_IDENTITY_VERSION,
                    event.source,
                    event.type,
                    event.token,
                    event.signature,
                    event.slot,
                    event.ts,
                    now,
                    payload_json,
                ),
            )
            row = conn.execute("SELECT * FROM worker_events WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                raise RuntimeError("worker_event_claim_missing_after_insert")
            status = str(row["status"] or "")
            attempt_count = int(row["attempt_count"] or 0)
            if status == "completed":
                conn.commit()
                return EventClaim(event_id, False, True, False, False, attempt_count, status, "completed_duplicate")
            if status == "dead_letter":
                conn.commit()
                return EventClaim(event_id, False, True, False, False, attempt_count, status, "dead_letter")
            if status == "processing" and int(row["lease_expires_ts"] or 0) > now and row["lease_owner"] != worker_id:
                conn.commit()
                return EventClaim(event_id, False, False, True, False, attempt_count, status, "active_lease")
            if attempt_count >= max_attempts:
                self._record_dead_letter_in_tx(
                    conn,
                    object_type="event",
                    object_id=event_id,
                    event_id=event_id,
                    failure_stage="claim",
                    attempt_count=attempt_count,
                    payload_json=payload_json,
                    error_type=str(row["last_error_type"] or "MaxAttemptsReached"),
                    error_message=str(row["last_error"] or "maximum event attempts reached"),
                    now=now,
                )
                conn.execute(
                    "UPDATE worker_events SET status='dead_letter', lease_owner=NULL, lease_expires_ts=NULL, last_attempt_ts=? WHERE event_id=?",
                    (now, event_id),
                )
                conn.commit()
                return EventClaim(event_id, False, False, False, False, attempt_count, "dead_letter", "max_attempts")
            reclaimed = status == "processing"
            first_attempt = int(row["first_attempt_ts"] or now)
            attempt_count += 1
            conn.execute(
                """
                UPDATE worker_events
                SET status='processing',
                    payload_json=?,
                    attempt_count=?,
                    lease_owner=?,
                    lease_expires_ts=?,
                    first_attempt_ts=?,
                    last_attempt_ts=?,
                    last_error_type=NULL,
                    last_error=NULL
                WHERE event_id=?
                """,
                (payload_json, attempt_count, worker_id, lease_expires, first_attempt, now, event_id),
            )
            conn.commit()
        return EventClaim(event_id, True, False, False, reclaimed, attempt_count, "processing", "reclaimed" if reclaimed else "claimed")

    def complete_event(self, event_id: str, *, completed_ts: int | None = None) -> None:
        now = int(completed_ts or time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE worker_events
                SET status='completed', completed_ts=?, lease_owner=NULL, lease_expires_ts=NULL, last_error_type=NULL, last_error=NULL
                WHERE event_id=?
                """,
                (now, event_id),
            )

    def fail_event(
        self,
        event_id: str,
        *,
        error: BaseException | str,
        failure_stage: str = "process_event",
        max_attempts: int | None = None,
    ) -> EventClaim:
        now = int(time.time())
        max_attempts = worker_v2_max_event_attempts() if max_attempts is None else max(1, int(max_attempts))
        error_type = type(error).__name__ if isinstance(error, BaseException) else "Error"
        error_message = sanitize_error_message(error)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM worker_events WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                raise KeyError(event_id)
            attempt_count = int(row["attempt_count"] or 0)
            payload_json = str(row["payload_json"] or "{}")
            terminal = attempt_count >= max_attempts
            status = "dead_letter" if terminal else "failed"
            conn.execute(
                """
                UPDATE worker_events
                SET status=?, lease_owner=NULL, lease_expires_ts=NULL, last_attempt_ts=?,
                    last_error_type=?, last_error=?
                WHERE event_id=?
                """,
                (status, now, error_type, error_message, event_id),
            )
            if terminal:
                self._record_dead_letter_in_tx(
                    conn,
                    object_type="event",
                    object_id=event_id,
                    event_id=event_id,
                    failure_stage=failure_stage,
                    attempt_count=attempt_count,
                    payload_json=payload_json,
                    error_type=error_type,
                    error_message=error_message,
                    now=now,
                )
            conn.commit()
        return EventClaim(event_id, False, False, False, False, attempt_count, status, "max_attempts" if terminal else "failed")

    def record_decision(
        self,
        *,
        event_id: str,
        derived_event_type: str,
        token: str | None,
        route_tier: str | None,
        disposition: str,
        reason: str | None,
        payload: dict[str, Any] | None = None,
        decision_id: str | None = None,
    ) -> str:
        if disposition not in DECISION_DISPOSITIONS:
            raise ValueError(f"unknown decision disposition: {disposition}")
        now = int(time.time())
        decision_id = decision_id or uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO worker_dispatch_decisions (
                    decision_id, event_id, derived_event_type, token, route_tier, disposition,
                    reason, payload_json, created_ts, updated_ts
                ) VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    COALESCE((SELECT created_ts FROM worker_dispatch_decisions WHERE decision_id=?), ?),
                    ?
                )
                """,
                (
                    decision_id,
                    event_id,
                    derived_event_type,
                    token,
                    route_tier,
                    disposition,
                    reason,
                    _canonical_json(_safe_payload(payload or {})),
                    decision_id,
                    now,
                    now,
                ),
            )
        return decision_id

    def update_decision(
        self,
        decision_id: str,
        *,
        disposition: str | None = None,
        reason: str | None = None,
        delivered_ts: int | None = None,
        delivery_id: str | None = None,
        legacy_signal_id: str | None = None,
    ) -> None:
        fields = ["updated_ts=?"]
        values: list[Any] = [int(time.time())]
        if disposition is not None:
            if disposition not in DECISION_DISPOSITIONS:
                raise ValueError(f"unknown decision disposition: {disposition}")
            fields.append("disposition=?")
            values.append(disposition)
        if reason is not None:
            fields.append("reason=?")
            values.append(reason)
        if delivered_ts is not None:
            fields.append("delivered_ts=?")
            values.append(delivered_ts)
        if delivery_id is not None:
            fields.append("delivery_id=?")
            values.append(delivery_id)
        if legacy_signal_id is not None:
            fields.append("legacy_signal_id=?")
            values.append(legacy_signal_id)
        values.append(decision_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE worker_dispatch_decisions SET {', '.join(fields)} WHERE decision_id=?", values)

    def create_outbox(
        self,
        *,
        decision_id: str,
        event_id: str,
        channel: str,
        operation: str,
        destination_key: str,
        token: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
        edit_message_id: str | None = None,
        status: str = "pending",
        delivery_id: str | None = None,
    ) -> str:
        if status not in OUTBOX_STATUSES:
            raise ValueError(f"unknown outbox status: {status}")
        now = int(time.time())
        delivery_id = delivery_id or uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO worker_delivery_outbox (
                    delivery_id, decision_id, event_id, channel, operation, destination_key,
                    token, event_type, payload_json, edit_message_id, status, created_ts, updated_ts
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT created_ts FROM worker_delivery_outbox WHERE delivery_id=?), ?),
                    ?
                )
                """,
                (
                    delivery_id,
                    decision_id,
                    event_id,
                    channel,
                    operation,
                    destination_key,
                    token,
                    event_type,
                    _canonical_json(_safe_payload(payload or {})),
                    edit_message_id,
                    status,
                    delivery_id,
                    now,
                    now,
                ),
            )
        return delivery_id

    def mark_outbox_attempting(self, delivery_id: str) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE worker_delivery_outbox
                SET status='attempting', attempt_count=attempt_count + 1, attempt_started_ts=?, updated_ts=?
                WHERE delivery_id=?
                """,
                (now, now, delivery_id),
            )

    def update_outbox_result(self, delivery_id: str, *, result: Any) -> None:
        now = int(time.time())
        success = bool(getattr(result, "success", False))
        ambiguous = bool(getattr(result, "ambiguous", False))
        status = "sent" if success else "delivery_uncertain" if ambiguous else "failed"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE worker_delivery_outbox
                SET status=?, updated_ts=?, delivered_ts=?, external_message_id=?, status_code=?,
                    failure_reason=?, error_type=?, error_message=?, retryable=?, next_attempt_ts=?
                WHERE delivery_id=?
                """,
                (
                    status,
                    now,
                    now if success else None,
                    getattr(result, "message_id", None),
                    getattr(result, "status_code", None),
                    getattr(result, "reason", None),
                    getattr(result, "error_type", None),
                    sanitize_error_message(getattr(result, "error_message", None)),
                    1 if getattr(result, "retryable", False) else 0,
                    None,
                    delivery_id,
                ),
            )

    def reserve_cooldown(
        self,
        cooldown_key: str,
        cooldown_seconds: int,
        reservation_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        now_ts: int | None = None,
    ) -> CooldownDecision:
        now = int(now_ts or time.time())
        cooldown_seconds = max(0, int(cooldown_seconds or 0))
        reservation_ttl = max(cooldown_seconds, worker_v2_event_lease_seconds(), 60)
        expires = now + reservation_ttl
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM worker_cooldowns WHERE cooldown_key=?", (cooldown_key,)).fetchone()
            if row:
                last_delivered = row["last_delivered_ts"]
                active_reservation = row["reservation_id"] and int(row["reservation_expires_ts"] or 0) > now
                if active_reservation and row["reservation_id"] != reservation_id:
                    conn.commit()
                    return CooldownDecision(cooldown_key, False, False, "active_reservation", last_delivered, row["reservation_id"], row["reservation_expires_ts"])
                if last_delivered is not None and now - int(last_delivered) < cooldown_seconds:
                    conn.commit()
                    return CooldownDecision(cooldown_key, False, False, "cooldown_active", int(last_delivered), row["reservation_id"], row["reservation_expires_ts"])
                conn.execute(
                    """
                    UPDATE worker_cooldowns
                    SET reservation_id=?, reservation_started_ts=?, reservation_expires_ts=?, updated_ts=?, metadata_json=?
                    WHERE cooldown_key=?
                    """,
                    (reservation_id, now, expires, now, _canonical_json(_safe_payload(metadata or {})), cooldown_key),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO worker_cooldowns (
                        cooldown_key, last_delivered_ts, reservation_id, reservation_started_ts,
                        reservation_expires_ts, updated_ts, metadata_json
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?)
                    """,
                    (cooldown_key, reservation_id, now, expires, now, _canonical_json(_safe_payload(metadata or {}))),
                )
            conn.commit()
        return CooldownDecision(cooldown_key, True, True, "reserved", None, reservation_id, expires)

    def commit_cooldown(self, cooldown_key: str, reservation_id: str, delivered_ts: int | None = None) -> bool:
        now = int(delivered_ts or time.time())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT reservation_id FROM worker_cooldowns WHERE cooldown_key=?", (cooldown_key,)).fetchone()
            if not row or row["reservation_id"] != reservation_id:
                conn.commit()
                return False
            conn.execute(
                """
                UPDATE worker_cooldowns
                SET last_delivered_ts=?, reservation_id=NULL, reservation_started_ts=NULL,
                    reservation_expires_ts=NULL, updated_ts=?
                WHERE cooldown_key=?
                """,
                (now, now, cooldown_key),
            )
            conn.commit()
        return True

    def release_cooldown(self, cooldown_key: str, reservation_id: str, reason: str | None = None) -> bool:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT reservation_id, metadata_json FROM worker_cooldowns WHERE cooldown_key=?", (cooldown_key,)).fetchone()
            if not row or row["reservation_id"] != reservation_id:
                conn.commit()
                return False
            metadata = {}
            if row["metadata_json"]:
                try:
                    metadata = json.loads(row["metadata_json"])
                except Exception:
                    metadata = {}
            if reason:
                metadata["release_reason"] = reason
            conn.execute(
                """
                UPDATE worker_cooldowns
                SET reservation_id=NULL, reservation_started_ts=NULL, reservation_expires_ts=NULL,
                    updated_ts=?, metadata_json=?
                WHERE cooldown_key=?
                """,
                (now, _canonical_json(_safe_payload(metadata)), cooldown_key),
            )
            conn.commit()
        return True

    def advance_checkpoint(
        self,
        checkpoint_key: str,
        *,
        source: str,
        stage: str,
        slot: int | None,
        signature: str | None,
        event_id: str | None,
        observed_ts: float | None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT slot FROM worker_checkpoints WHERE checkpoint_key=?", (checkpoint_key,)).fetchone()
            if row and row["slot"] is not None and slot is not None and int(slot) < int(row["slot"]):
                conn.commit()
                return False
            conn.execute(
                """
                INSERT INTO worker_checkpoints (
                    checkpoint_key, source, stage, slot, signature, event_id, observed_ts, updated_ts, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_key) DO UPDATE SET
                    source=excluded.source,
                    stage=excluded.stage,
                    slot=COALESCE(excluded.slot, worker_checkpoints.slot),
                    signature=excluded.signature,
                    event_id=excluded.event_id,
                    observed_ts=excluded.observed_ts,
                    updated_ts=excluded.updated_ts,
                    metadata_json=excluded.metadata_json
                """,
                (checkpoint_key, source, stage, slot, signature, event_id, observed_ts, now, _canonical_json(_safe_payload(metadata or {}))),
            )
            conn.commit()
        return True

    def _record_dead_letter_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        object_type: str,
        object_id: str,
        event_id: str | None,
        failure_stage: str,
        attempt_count: int,
        payload_json: str,
        error_type: str,
        error_message: str,
        now: int,
    ) -> str:
        dead_letter_id = hashlib.sha256(f"{object_type}:{object_id}".encode("utf-8")).hexdigest()
        conn.execute(
            """
            INSERT INTO worker_dead_letters (
                dead_letter_id, object_type, object_id, event_id, failure_stage, attempt_count,
                payload_json, error_type, error_message, created_ts, last_failed_ts, replay_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT(object_type, object_id) DO UPDATE SET
                failure_stage=excluded.failure_stage,
                attempt_count=excluded.attempt_count,
                payload_json=excluded.payload_json,
                error_type=excluded.error_type,
                error_message=excluded.error_message,
                last_failed_ts=excluded.last_failed_ts
            """,
            (
                dead_letter_id,
                object_type,
                object_id,
                event_id,
                failure_stage,
                attempt_count,
                payload_json,
                error_type,
                error_message,
                now,
                now,
            ),
        )
        return dead_letter_id

    def list_recent_dead_letters(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM worker_dead_letters ORDER BY created_ts DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_dead_letter(self, dead_letter_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM worker_dead_letters WHERE dead_letter_id=?", (dead_letter_id,)).fetchone()
        return dict(row) if row else None

    def mark_dead_letter_reviewed(self, dead_letter_id: str) -> bool:
        return self._set_dead_letter_replay_status(dead_letter_id, "reviewed")

    def reset_dead_letter_to_replayable(self, dead_letter_id: str) -> bool:
        return self._set_dead_letter_replay_status(dead_letter_id, "replayable")

    def _set_dead_letter_replay_status(self, dead_letter_id: str, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE worker_dead_letters SET replay_status=?, last_failed_ts=? WHERE dead_letter_id=?",
                (status, int(time.time()), dead_letter_id),
            )
            return cur.rowcount > 0

    def health_summary(
        self,
        *,
        worker_v2_enabled: bool,
        worker_instance_id: str | None,
        critical_task_health: dict[str, Any] | None = None,
        optional_task_restart_counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        now = int(time.time())
        with self._connect() as conn:
            event_counts = {
                str(row["status"]): int(row["count"] or 0)
                for row in conn.execute("SELECT status, COUNT(*) AS count FROM worker_events GROUP BY status").fetchall()
            }
            outbox_counts = {
                str(row["status"]): int(row["count"] or 0)
                for row in conn.execute("SELECT status, COUNT(*) AS count FROM worker_delivery_outbox GROUP BY status").fetchall()
            }
            oldest = conn.execute("SELECT MIN(received_ts) AS oldest FROM worker_events WHERE status='received'").fetchone()
            active_leases = conn.execute(
                "SELECT COUNT(*) AS count FROM worker_events WHERE status='processing' AND lease_expires_ts > ?",
                (now,),
            ).fetchone()
            latest_success = conn.execute("SELECT MAX(delivered_ts) AS ts FROM worker_delivery_outbox WHERE status='sent'").fetchone()
            latest_failed = conn.execute("SELECT MAX(updated_ts) AS ts FROM worker_delivery_outbox WHERE status IN ('failed', 'delivery_uncertain')").fetchone()
            cooldown_count = conn.execute("SELECT COUNT(*) AS count FROM worker_cooldowns").fetchone()
            checkpoints = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT checkpoint_key, source, stage, slot, signature, event_id, observed_ts, updated_ts
                    FROM worker_checkpoints
                    ORDER BY updated_ts DESC
                    LIMIT 20
                    """
                ).fetchall()
            ]
        oldest_ts = oldest["oldest"] if oldest else None
        return {
            "enabled": worker_v2_enabled,
            "worker_instance_id": worker_instance_id,
            "pending_event_count": event_counts.get("received", 0),
            "processing_event_count": event_counts.get("processing", 0),
            "failed_event_count": event_counts.get("failed", 0),
            "dead_letter_count": event_counts.get("dead_letter", 0),
            "oldest_pending_event_age_seconds": now - int(oldest_ts) if oldest_ts else None,
            "active_event_leases": int(active_leases["count"] or 0) if active_leases else 0,
            "pending_outbox_count": outbox_counts.get("pending", 0),
            "attempting_outbox_count": outbox_counts.get("attempting", 0),
            "failed_outbox_count": outbox_counts.get("failed", 0),
            "uncertain_delivery_count": outbox_counts.get("delivery_uncertain", 0),
            "most_recent_successful_delivery": latest_success["ts"] if latest_success else None,
            "most_recent_failed_delivery": latest_failed["ts"] if latest_failed else None,
            "cooldown_count": int(cooldown_count["count"] or 0) if cooldown_count else 0,
            "latest_checkpoints_by_source": checkpoints,
            "critical_task_health": critical_task_health or {},
            "optional_task_restart_counts": optional_task_restart_counts or {},
        }


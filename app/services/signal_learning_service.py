from __future__ import annotations

import asyncio
import html
import httpx
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services.db_service import connect_sqlite, resolve_engine_db_path
from app.services.structured_logging import log_event
from worker.dex import dex_enrich_token, select_best_pair, summarize_pair

try:
    from zoneinfo import ZoneInfo

    CHICAGO_TZ = ZoneInfo("America/Chicago")
except Exception:
    CHICAGO_TZ = timezone.utc


logger = logging.getLogger(__name__)

DB_PATH = resolve_engine_db_path()
SNAPSHOT_HORIZONS_MINUTES = (5, 15, 60, 240)
SNAPSHOT_POLL_SECONDS = 30
REPORT_POLL_SECONDS = 600
_SCHEMA_READY = False
DEFAULT_POLICY_NAME = "deterministic_engine"
DEFAULT_POLICY_VERSION = "deterministic-v1"
_REMOTE_WRITE_TIMEOUT = 5.0
_POSITIVE_OUTCOME_LABELS = {"worked", "strong_continuation"}
_NEGATIVE_OUTCOME_LABELS = {"failed", "faded"}


def _connect() -> sqlite3.Connection:
    return connect_sqlite(_current_db_path())


def _current_db_path() -> Path:
    return resolve_engine_db_path(DB_PATH)


def _learning_process_role() -> str:
    role = os.getenv("SIGNAL_ENGINE_PROCESS_ROLE", "").strip().lower()
    return role or "unknown"


def _learning_write_base_url() -> str:
    explicit = os.getenv("SIGNAL_ENGINE_LEARNING_WRITE_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    fallback = os.getenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if fallback:
        return fallback
    return ""


def _learning_write_token() -> str:
    return os.getenv("SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN", "").strip()


def _learning_write_mode() -> str:
    explicit = os.getenv("SIGNAL_ENGINE_LEARNING_WRITE_MODE", "").strip().lower()
    if explicit in {"local", "remote", "mirror"}:
        return explicit
    shared_env_set = bool(os.getenv("SIGNAL_ENGINE_DB_PATH", "").strip() or os.getenv("STATE_ENGINE_DB_PATH", "").strip())
    if _learning_process_role() == "worker" and not shared_env_set and _learning_write_base_url():
        return "remote"
    return "local"


def _learning_write_config() -> dict[str, Any]:
    db_path = _current_db_path()
    base_url = _learning_write_base_url()
    mode = _learning_write_mode()
    return {
        "mode": mode,
        "process_role": _learning_process_role(),
        "db_path": str(db_path),
        "db_path_env": (
            os.getenv("SIGNAL_ENGINE_DB_PATH", "").strip()
            or os.getenv("STATE_ENGINE_DB_PATH", "").strip()
        ),
        "shared_db_env_set": bool(os.getenv("SIGNAL_ENGINE_DB_PATH", "").strip() or os.getenv("STATE_ENGINE_DB_PATH", "").strip()),
        "remote_base_url": base_url,
        "remote_enabled": bool(base_url and mode in {"remote", "mirror"}),
        "remote_token_configured": bool(_learning_write_token()),
    }


def _internal_write_headers() -> dict[str, str]:
    token = _learning_write_token()
    if not token:
        return {}
    return {"X-Signal-Engine-Token": token}


def _post_internal_learning_write(endpoint: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    base_url = _learning_write_base_url()
    if not base_url:
        return None
    try:
        response = httpx.post(
            f"{base_url}{endpoint}",
            json=payload,
            headers=_internal_write_headers(),
            timeout=_REMOTE_WRITE_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else None
    except Exception:
        logger.exception("[signal-learning] remote_write_failed endpoint=%s base_url=%s", endpoint, base_url)
        return None


def init() -> None:
    global _SCHEMA_READY
    with _connect() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                signal_id TEXT PRIMARY KEY,
                external_ref TEXT,
                token TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT,
                creator TEXT,
                alert_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                lifecycle TEXT,
                confidence_score REAL,
                attention_score REAL,
                risk_score REAL,
                elite_score INTEGER,
                market_cap_usd REAL,
                liquidity_usd REAL,
                volume_m5_usd REAL,
                age_minutes REAL,
                price_change_m5 REAL,
                price_change_h1 REAL,
                txns_m5_buys INTEGER,
                txns_m5_sells INTEGER,
                hour_utc INTEGER,
                day_of_week_utc INTEGER,
                is_weekend_utc INTEGER,
                hour_local INTEGER,
                day_of_week_local INTEGER,
                local_daypart TEXT,
                session_bucket TEXT,
                payload_json TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_snapshot_jobs (
                signal_id TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                due_ts INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                PRIMARY KEY (signal_id, horizon_minutes)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_snapshots (
                signal_id TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                captured_ts INTEGER NOT NULL,
                lifecycle TEXT,
                market_cap_usd REAL,
                liquidity_usd REAL,
                volume_m5_usd REAL,
                age_minutes REAL,
                price_change_m5 REAL,
                price_change_h1 REAL,
                txns_m5_buys INTEGER,
                txns_m5_sells INTEGER,
                market_cap_change_pct REAL,
                liquidity_change_pct REAL,
                volume_m5_change_pct REAL,
                outcome_label TEXT,
                snapshot_json TEXT,
                PRIMARY KEY (signal_id, horizon_minutes)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_reports (
                report_date TEXT PRIMARY KEY,
                generated_ts INTEGER NOT NULL,
                report_json TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS tuning_approvals (
                approval_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL,
                approved_by TEXT,
                approval_kind TEXT NOT NULL,
                target_name TEXT,
                artifact_kind TEXT NOT NULL,
                lookback_hours INTEGER NOT NULL,
                rollout_status TEXT NOT NULL DEFAULT 'pending',
                rolled_out_ts INTEGER,
                deployment_service TEXT,
                deployment_sha TEXT,
                deployment_env TEXT,
                verification_status TEXT,
                verification_ts INTEGER,
                verification_summary TEXT,
                notes TEXT,
                artifact_text TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS rollout_notifications (
                notification_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                level TEXT NOT NULL,
                target_name TEXT,
                approval_id TEXT,
                deployment_service TEXT,
                deployment_sha TEXT,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'pending',
                delivered_ts INTEGER,
                last_error TEXT,
                acknowledged_ts INTEGER,
                acknowledged_by TEXT,
                snoozed_until_ts INTEGER,
                resolved_ts INTEGER,
                resolved_by TEXT,
                resolution_note TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_decisions (
                decision_id TEXT PRIMARY KEY,
                token TEXT,
                event_type TEXT,
                stage TEXT,
                decision TEXT NOT NULL,
                action_taken TEXT,
                policy_name TEXT,
                policy_version TEXT,
                reasons_json TEXT,
                features_json TEXT,
                attention_score REAL,
                risk_score REAL,
                confidence_score REAL,
                creator_score REAL,
                lifecycle TEXT,
                hour_utc INTEGER,
                day_of_week_utc INTEGER,
                is_weekend_utc INTEGER,
                hour_local INTEGER,
                day_of_week_local INTEGER,
                local_daypart TEXT,
                session_bucket TEXT,
                created_ts INTEGER NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_replay_runs (
                run_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL,
                hours INTEGER NOT NULL,
                trace_limit INTEGER NOT NULL,
                stage TEXT,
                baseline_policy_name TEXT,
                baseline_policy_version TEXT,
                shadow_policy_name TEXT NOT NULL,
                shadow_policy_version TEXT NOT NULL,
                overrides_json TEXT NOT NULL,
                summary_json TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_replay_results (
                run_id TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                signal_id TEXT,
                token TEXT,
                stage TEXT,
                current_action TEXT,
                shadow_action TEXT,
                changed INTEGER NOT NULL DEFAULT 0,
                outcome_label TEXT,
                market_cap_change_pct REAL,
                features_json TEXT,
                PRIMARY KEY (run_id, decision_id)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_profiles (
                profile_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL,
                policy_name TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                description TEXT,
                config_json TEXT NOT NULL,
                created_by TEXT,
                UNIQUE(policy_name, policy_version)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_rollouts (
                rollout_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL,
                policy_name TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                rollout_mode TEXT NOT NULL,
                rollout_status TEXT NOT NULL DEFAULT 'active',
                stage_scope TEXT,
                regime_scope TEXT,
                traffic_percent INTEGER NOT NULL DEFAULT 100,
                priority INTEGER NOT NULL DEFAULT 100,
                activated_by TEXT,
                notes TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_approvals (
                approval_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL,
                policy_name TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref TEXT,
                approval_status TEXT NOT NULL DEFAULT 'draft',
                approved_by TEXT,
                approved_ts INTEGER,
                notes TEXT,
                summary_json TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_rollout_events (
                event_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL,
                rollout_id TEXT,
                approval_id TEXT,
                policy_name TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_status TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_automation_runs (
                run_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL,
                completed_ts INTEGER,
                hours INTEGER NOT NULL,
                replay_limit INTEGER NOT NULL,
                status TEXT NOT NULL,
                summary_json TEXT NOT NULL
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_signals_alert_ts ON signals(alert_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_signal_jobs_due ON signal_snapshot_jobs(status, due_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_signal ON signal_snapshots(signal_id, horizon_minutes)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tuning_approvals_ts ON tuning_approvals(created_ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rollout_notifications_ts ON rollout_notifications(created_ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ts ON signal_decisions(created_ts, decision)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_policy_replay_runs_ts ON policy_replay_runs(created_ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_policy_replay_results_run ON policy_replay_results(run_id, changed)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_policy_profiles_name ON policy_profiles(policy_name, created_ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_policy_rollouts_active ON policy_rollouts(rollout_status, priority, created_ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_policy_approvals_ts ON policy_approvals(created_ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_policy_rollout_events_ts ON policy_rollout_events(created_ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_policy_automation_runs_ts ON policy_automation_runs(created_ts DESC)")
        approval_cols = {row[1] for row in c.execute("PRAGMA table_info(tuning_approvals)").fetchall()}
        if "rollout_status" not in approval_cols:
            c.execute("ALTER TABLE tuning_approvals ADD COLUMN rollout_status TEXT NOT NULL DEFAULT 'pending'")
        if "rolled_out_ts" not in approval_cols:
            c.execute("ALTER TABLE tuning_approvals ADD COLUMN rolled_out_ts INTEGER")
        if "deployment_service" not in approval_cols:
            c.execute("ALTER TABLE tuning_approvals ADD COLUMN deployment_service TEXT")
        if "deployment_sha" not in approval_cols:
            c.execute("ALTER TABLE tuning_approvals ADD COLUMN deployment_sha TEXT")
        if "deployment_env" not in approval_cols:
            c.execute("ALTER TABLE tuning_approvals ADD COLUMN deployment_env TEXT")
        if "verification_status" not in approval_cols:
            c.execute("ALTER TABLE tuning_approvals ADD COLUMN verification_status TEXT")
        if "verification_ts" not in approval_cols:
            c.execute("ALTER TABLE tuning_approvals ADD COLUMN verification_ts INTEGER")
        if "verification_summary" not in approval_cols:
            c.execute("ALTER TABLE tuning_approvals ADD COLUMN verification_summary TEXT")
        notification_cols = {row[1] for row in c.execute("PRAGMA table_info(rollout_notifications)").fetchall()}
        if "acknowledged_ts" not in notification_cols:
            c.execute("ALTER TABLE rollout_notifications ADD COLUMN acknowledged_ts INTEGER")
        if "acknowledged_by" not in notification_cols:
            c.execute("ALTER TABLE rollout_notifications ADD COLUMN acknowledged_by TEXT")
        if "snoozed_until_ts" not in notification_cols:
            c.execute("ALTER TABLE rollout_notifications ADD COLUMN snoozed_until_ts INTEGER")
        if "resolved_ts" not in notification_cols:
            c.execute("ALTER TABLE rollout_notifications ADD COLUMN resolved_ts INTEGER")
        if "resolved_by" not in notification_cols:
            c.execute("ALTER TABLE rollout_notifications ADD COLUMN resolved_by TEXT")
        if "resolution_note" not in notification_cols:
            c.execute("ALTER TABLE rollout_notifications ADD COLUMN resolution_note TEXT")
        decision_cols = {row[1] for row in c.execute("PRAGMA table_info(signal_decisions)").fetchall()}
        if "signal_id" not in decision_cols:
            c.execute("ALTER TABLE signal_decisions ADD COLUMN signal_id TEXT")
        if "action_taken" not in decision_cols:
            c.execute("ALTER TABLE signal_decisions ADD COLUMN action_taken TEXT")
        if "policy_name" not in decision_cols:
            c.execute("ALTER TABLE signal_decisions ADD COLUMN policy_name TEXT")
        if "policy_version" not in decision_cols:
            c.execute("ALTER TABLE signal_decisions ADD COLUMN policy_version TEXT")
        if "features_json" not in decision_cols:
            c.execute("ALTER TABLE signal_decisions ADD COLUMN features_json TEXT")
        rollout_cols = {row[1] for row in c.execute("PRAGMA table_info(policy_rollouts)").fetchall()}
        if "regime_scope" not in rollout_cols:
            c.execute("ALTER TABLE policy_rollouts ADD COLUMN regime_scope TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_decisions_signal_id ON signal_decisions(signal_id)")
    _SCHEMA_READY = True


def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    init()


def get_learning_storage_status() -> dict[str, Any]:
    _ensure_schema()
    db_path = _current_db_path()
    file_exists = db_path.exists()
    file_size_bytes = db_path.stat().st_size if file_exists else 0
    write_config = _learning_write_config()
    with _connect() as c:
        signal_count = int(c.execute("SELECT COUNT(1) FROM signals").fetchone()[0] or 0)
        decision_count = int(c.execute("SELECT COUNT(1) FROM signal_decisions").fetchone()[0] or 0)
        snapshot_count = int(c.execute("SELECT COUNT(1) FROM signal_snapshots").fetchone()[0] or 0)
    return {
        "db_path": str(db_path),
        "db_path_env": (
            os.getenv("SIGNAL_ENGINE_DB_PATH", "").strip()
            or os.getenv("STATE_ENGINE_DB_PATH", "").strip()
        ),
        "file_exists": file_exists,
        "file_size_bytes": file_size_bytes,
        "signal_count": signal_count,
        "decision_count": decision_count,
        "snapshot_count": snapshot_count,
        "write_config": write_config,
    }


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _extract_extra_metrics(extra: dict[str, Any] | None) -> dict[str, Any]:
    extra = extra if isinstance(extra, dict) else {}
    dex_summary = extra.get("dex_summary") if isinstance(extra.get("dex_summary"), dict) else {}
    return {
        "lifecycle": str(extra.get("lifecycle") or ("dex" if dex_summary else "unknown")),
        "attention_score": _to_float(extra.get("attention_score")),
        "risk_score": _to_float(extra.get("risk_score")),
        "elite_score": _to_int(extra.get("elite_score")),
        "market_cap_usd": _to_float(dex_summary.get("market_cap") or dex_summary.get("fdv")),
        "liquidity_usd": _to_float(dex_summary.get("liquidity_usd")),
        "volume_m5_usd": _to_float(dex_summary.get("volume_m5")),
        "age_minutes": _to_float(dex_summary.get("age_minutes")),
        "price_change_m5": _to_float(dex_summary.get("price_change_m5")),
        "price_change_h1": _to_float(dex_summary.get("price_change_h1")),
        "txns_m5_buys": _to_int(dex_summary.get("txns_m5_buys")),
        "txns_m5_sells": _to_int(dex_summary.get("txns_m5_sells")),
    }


def _classify_time_features(ts_value: float) -> dict[str, Any]:
    dt_utc = datetime.fromtimestamp(ts_value, tz=timezone.utc)
    dt_local = dt_utc.astimezone(CHICAGO_TZ)
    hour_utc = dt_utc.hour
    if 0 <= hour_utc < 8:
        session = "asia"
    elif 8 <= hour_utc < 14:
        session = "europe"
    elif 14 <= hour_utc < 21:
        session = "us_day"
    else:
        session = "late_us"

    local_hour = dt_local.hour
    if 0 <= local_hour < 6:
        daypart = "night"
    elif 6 <= local_hour < 12:
        daypart = "morning"
    elif 12 <= local_hour < 18:
        daypart = "afternoon"
    else:
        daypart = "evening"

    return {
        "hour_utc": hour_utc,
        "day_of_week_utc": dt_utc.weekday(),
        "is_weekend_utc": 1 if dt_utc.weekday() >= 5 else 0,
        "hour_local": local_hour,
        "day_of_week_local": dt_local.weekday(),
        "local_daypart": daypart,
        "session_bucket": session,
    }


def _percent_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return round(((current - baseline) / baseline) * 100.0, 2)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _policy_env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "").strip()
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)


def _policy_env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name, "").strip()
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _default_policy_descriptor() -> dict[str, Any]:
    version = (
        os.getenv("SIGNAL_ENGINE_POLICY_VERSION", "").strip()
        or os.getenv("RENDER_GIT_COMMIT", "").strip()
        or DEFAULT_POLICY_VERSION
    )
    return {
        "policy_name": os.getenv("SIGNAL_ENGINE_POLICY_NAME", "").strip() or DEFAULT_POLICY_NAME,
        "policy_version": version,
        "candidate_attention_min": _policy_env_float("ATTENTION_CANDIDATE_THRESHOLD", 0.70),
        "candidate_creator_min": _policy_env_float("EARLY_CREATOR_MIN", 0.30),
        "candidate_gate_attention_min": _policy_env_float("SIGNAL_ENGINE_CANDIDATE_GATE_ATTENTION_MIN", 0.14),
        "candidate_gate_min_age_sec": _policy_env_int("SIGNAL_ENGINE_CANDIDATE_GATE_MIN_AGE_SEC", 15),
        "promoted_confidence_min": _policy_env_float("SIGNAL_ENGINE_PROMOTED_CONFIDENCE_MIN", 0.80),
        "promoted_attention_min": _policy_env_float("PROMOTION_MIN_ATTENTION", 0.50),
        "promoted_risk_max": _policy_env_float("PROMOTION_MAX_RISK", 0.60),
        "promoted_liquidity_min": _policy_env_float("PROM_MIN_LIQ_USD", 15000.0),
        "promoted_buyers_15m_min": _policy_env_int("SIGNAL_ENGINE_PROMOTED_BUYERS_15M_MIN", 30),
    }


def _normalize_policy_descriptor(
    *,
    policy_name: str | None = None,
    policy_version: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    descriptor = _default_policy_descriptor()
    if policy_name:
        descriptor["policy_name"] = policy_name
    if policy_version:
        descriptor["policy_version"] = policy_version
    for key, value in (overrides or {}).items():
        if value is not None:
            descriptor[key] = value
    return descriptor


def _policy_fingerprint(token: str | None) -> int:
    raw = str(token or "")
    if not raw:
        return 0
    return sum(ord(ch) for ch in raw) % 100


def _policy_config_fingerprint(config: dict[str, Any] | None) -> str:
    descriptor = dict(config or {})
    descriptor.pop("policy_name", None)
    descriptor.pop("policy_version", None)
    try:
        return json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    except Exception:
        return "{}"


def _json_loads_dict(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _json_loads_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _reason_family(reason: str) -> str:
    text = str(reason or "").strip().lower()
    if not text:
        return "unknown"
    if text.startswith("age") or "ttl" in text or "bypass" in text:
        return "timing"
    if "liq" in text or "volume" in text or "pool" in text or "market_support" in text:
        return "market_quality"
    if "wallet" in text or "holder" in text or "buyer" in text or "kol" in text or "tracked" in text:
        return "wallet_quality"
    if "risk" in text or "authority" in text or "creator" in text or "lp_drain" in text or "mint" in text:
        return "safety"
    if "attention" in text or "burst" in text or "momentum" in text or "sniper" in text:
        return "momentum"
    if "candidate" in text or "promotion" in text or "promoted" in text:
        return "promotion"
    return "other"


def _extract_bypass_flags(features: dict[str, Any]) -> list[str]:
    bypasses: list[str] = []
    if bool(features.get("age_bypass_eligible")) or bool(features.get("age_bypass_used")) or bool(features.get("age_bypass_until")):
        bypasses.append("age_bypass")
    if bool(features.get("liq_unknown_bypass")):
        bypasses.append("liq_unknown_bypass")
    if bool(features.get("sniper_fast_track")):
        bypasses.append("sniper_fast_track")
    if bool(features.get("candidate_rate_limit_bypass")):
        bypasses.append("candidate_rate_limit_bypass")
    return bypasses


def _decision_route_class(
    *,
    signal_event_type: str | None,
    stage: str | None,
    decision: str | None,
    action_taken: str | None,
    features: dict[str, Any],
) -> str:
    route_tier = str(features.get("route_tier") or "").strip().lower()
    signal_type = str(signal_event_type or "").strip().lower()
    decision_name = str(decision or "").strip().lower()
    stage_name = str(stage or "").strip().lower()
    action_name = str(action_taken or "").strip().lower()

    if stage_name in {"candidate", "heating_up"} and ("skip" in decision_name or "block" in decision_name):
        return "reject"
    if stage_name == "promoted" and "block" in decision_name:
        return "reject"
    if decision_name in {"candidate_buffered", "candidate_watch", "watch"}:
        return "watch"
    if signal_type == "promoted" or stage_name == "promoted" or decision_name.startswith("promotion_") and action_name == "emit":
        return "promoted"
    if signal_type == "heating_up":
        return "sniper" if route_tier == "sniper" or bool(features.get("sniper_ready")) else "heating_up"
    if signal_type == "candidate" and action_name == "emit":
        return "candidate"
    if action_name == "emit":
        if route_tier == "sniper":
            return "sniper"
        if stage_name == "heating_up" or route_tier == "heating_up":
            return "heating_up"
        if stage_name == "candidate":
            return "candidate"
    if route_tier == "sniper":
        return "sniper"
    if route_tier == "heating_up":
        return "heating_up"
    return "watch" if stage_name == "candidate" else "reject"


def _classify_validation_bucket(
    *,
    route_class: str,
    sent_to_discord: bool,
    outcome_label: str,
    market_cap_change_pct: float | None,
    age_minutes: float | None,
) -> str:
    outcome = str(outcome_label or "pending")
    mc_change = _to_float(market_cap_change_pct)
    age_value = _to_float(age_minutes)
    route = str(route_class or "unknown")

    if outcome in _NEGATIVE_OUTCOME_LABELS:
        return "noisy_heating_up" if route == "heating_up" else "false_positive"
    if outcome == "strong_continuation":
        if route in {"sniper", "promoted"} or (mc_change is not None and mc_change >= 75.0):
            return "elite_winner"
        return "too_early_but_valid" if route in {"candidate", "heating_up"} else "decent_signal"
    if outcome == "worked":
        if route in {"candidate", "heating_up"} and ((mc_change is not None and mc_change >= 35.0) or (age_value is not None and age_value <= 30.0)):
            return "too_early_but_valid"
        if route in {"promoted", "sniper"} and mc_change is not None and mc_change < 15.0:
            return "too_late"
        return "decent_signal"
    if not sent_to_discord:
        return "rejected"
    return "pending" if outcome == "pending" else "weak_alert"


def _policy_descriptor_from_features(
    features: dict[str, Any],
    *,
    policy_name: str | None,
    policy_version: str | None,
) -> dict[str, Any]:
    descriptor = features.get("policy_descriptor") if isinstance(features.get("policy_descriptor"), dict) else {}
    if descriptor:
        return dict(descriptor)
    return _normalize_policy_descriptor(policy_name=policy_name, policy_version=policy_version)


def _public_policy_profile(record: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    row = record
    config_json = row[5] if isinstance(row, tuple) else row["config_json"]
    try:
        config = json.loads(config_json or "{}")
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}
    return {
        "profile_id": row[0] if isinstance(row, tuple) else row["profile_id"],
        "created_ts": row[1] if isinstance(row, tuple) else row["created_ts"],
        "policy_name": row[2] if isinstance(row, tuple) else row["policy_name"],
        "policy_version": row[3] if isinstance(row, tuple) else row["policy_version"],
        "description": row[4] if isinstance(row, tuple) else row["description"],
        "config": config,
        "created_by": row[6] if isinstance(row, tuple) else row["created_by"],
    }


def create_policy_profile(
    *,
    policy_name: str,
    policy_version: str,
    config: dict[str, Any] | None = None,
    description: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    name = str(policy_name or "").strip()
    version = str(policy_version or "").strip()
    if not name:
        raise ValueError("policy_name_required")
    if not version:
        raise ValueError("policy_version_required")
    profile_id = uuid.uuid4().hex
    created_ts = int(time.time())
    resolved = _normalize_policy_descriptor(policy_name=name, policy_version=version, overrides=config)
    with _connect() as c:
        c.execute(
            """
            INSERT INTO policy_profiles (
                profile_id, created_ts, policy_name, policy_version, description, config_json, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                created_ts,
                name,
                version,
                description,
                _json_dumps(resolved),
                created_by,
            ),
        )
        row = c.execute(
            """
            SELECT profile_id, created_ts, policy_name, policy_version, description, config_json, created_by
            FROM policy_profiles
            WHERE profile_id=?
            """,
            (profile_id,),
        ).fetchone()
    return _public_policy_profile(row)


def list_policy_profiles(limit: int = 20, policy_name: str | None = None) -> list[dict[str, Any]]:
    _ensure_schema()
    clauses: list[str] = []
    params: list[Any] = []
    if policy_name:
        clauses.append("policy_name = ?")
        params.append(policy_name)
    params.append(max(1, limit))
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as c:
        rows = c.execute(
            f"""
            SELECT profile_id, created_ts, policy_name, policy_version, description, config_json, created_by
            FROM policy_profiles
            {where_sql}
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [_public_policy_profile(row) for row in rows]


def activate_policy_rollout(
    *,
    policy_name: str,
    policy_version: str,
    rollout_mode: str = "active",
    rollout_status: str = "active",
    stage_scope: str | None = None,
    regime_scope: str | None = None,
    traffic_percent: int = 100,
    priority: int = 100,
    activated_by: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    mode = str(rollout_mode or "").strip() or "active"
    if mode not in {"active", "shadow", "canary"}:
        raise ValueError("invalid_rollout_mode")
    name = str(policy_name or "").strip()
    version = str(policy_version or "").strip()
    if not name or not version:
        raise ValueError("policy_identity_required")
    if not list_policy_profiles(limit=1, policy_name=name):
        raise ValueError("policy_profile_not_found")
    rollout_id = uuid.uuid4().hex
    created_ts = int(time.time())
    effective_traffic = max(0, min(100, int(traffic_percent)))
    with _connect() as c:
        if mode == "active":
            c.execute(
                """
                UPDATE policy_rollouts
                SET rollout_status='superseded'
                WHERE rollout_status='active'
                  AND rollout_mode='active'
                  AND (stage_scope IS ? OR stage_scope = ?)
                  AND (regime_scope IS ? OR regime_scope = ?)
                """,
                (stage_scope, stage_scope, regime_scope, regime_scope),
            )
        c.execute(
            """
            INSERT INTO policy_rollouts (
                rollout_id, created_ts, policy_name, policy_version, rollout_mode, rollout_status,
                stage_scope, regime_scope, traffic_percent, priority, activated_by, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rollout_id,
                created_ts,
                name,
                version,
                mode,
                rollout_status,
                stage_scope,
                regime_scope,
                effective_traffic,
                priority,
                activated_by,
                notes,
            ),
        )
        row = c.execute(
            """
            SELECT rollout_id, created_ts, policy_name, policy_version, rollout_mode, rollout_status,
                   stage_scope, regime_scope, traffic_percent, priority, activated_by, notes
            FROM policy_rollouts
            WHERE rollout_id=?
            """,
            (rollout_id,),
        ).fetchone()
        c.execute(
            """
            INSERT INTO policy_rollout_events (
                event_id, created_ts, rollout_id, approval_id, policy_name, policy_version,
                event_type, event_status, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                created_ts,
                rollout_id,
                None,
                name,
                version,
                "rollout_activated",
                rollout_status,
                _json_dumps(
                    {
                        "rollout_mode": mode,
                        "stage_scope": stage_scope,
                        "regime_scope": regime_scope,
                        "traffic_percent": effective_traffic,
                        "priority": priority,
                        "activated_by": activated_by,
                        "notes": notes,
                    }
                ),
            ),
        )
    return {
        "rollout_id": row[0],
        "created_ts": row[1],
        "policy_name": row[2],
        "policy_version": row[3],
        "rollout_mode": row[4],
        "rollout_status": row[5],
        "stage_scope": row[6],
        "regime_scope": row[7],
        "traffic_percent": row[8],
        "priority": row[9],
        "activated_by": row[10],
        "notes": row[11],
    }


def create_policy_approval(
    *,
    policy_name: str,
    policy_version: str,
    source_type: str,
    source_ref: str | None = None,
    notes: str | None = None,
    approved_by: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    name = str(policy_name or "").strip()
    version = str(policy_version or "").strip()
    source = str(source_type or "").strip()
    if not name or not version:
        raise ValueError("policy_identity_required")
    if source not in {"profile", "replay"}:
        raise ValueError("invalid_source_type")
    profile = None
    for item in list_policy_profiles(limit=100, policy_name=name):
        if item["policy_version"] == version:
            profile = item
            break
    if profile is None:
        raise ValueError("policy_profile_not_found")
    approval_id = uuid.uuid4().hex
    created_ts = int(time.time())
    status = "approved" if approved_by else "draft"
    approved_ts = created_ts if approved_by else None
    summary = {
        "profile": profile,
        "source_type": source,
        "source_ref": source_ref,
    }
    with _connect() as c:
        c.execute(
            """
            INSERT INTO policy_approvals (
                approval_id, created_ts, policy_name, policy_version, source_type, source_ref,
                approval_status, approved_by, approved_ts, notes, summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                created_ts,
                name,
                version,
                source,
                source_ref,
                status,
                approved_by,
                approved_ts,
                notes,
                _json_dumps(summary),
            ),
        )
    return get_policy_approval(approval_id)  # type: ignore[return-value]


def get_policy_approval(approval_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    with _connect() as c:
        row = c.execute(
            """
            SELECT approval_id, created_ts, policy_name, policy_version, source_type, source_ref,
                   approval_status, approved_by, approved_ts, notes, summary_json
            FROM policy_approvals
            WHERE approval_id=?
            """,
            (approval_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "approval_id": row[0],
        "created_ts": row[1],
        "policy_name": row[2],
        "policy_version": row[3],
        "source_type": row[4],
        "source_ref": row[5],
        "approval_status": row[6],
        "approved_by": row[7],
        "approved_ts": row[8],
        "notes": row[9],
        "summary": json.loads(row[10] or "{}"),
    }


def list_policy_approvals(limit: int = 20, approval_status: str | None = None) -> list[dict[str, Any]]:
    _ensure_schema()
    params: list[Any] = []
    where_sql = ""
    if approval_status:
        where_sql = "WHERE approval_status=?"
        params.append(approval_status)
    params.append(max(1, limit))
    with _connect() as c:
        rows = c.execute(
            f"""
            SELECT approval_id
            FROM policy_approvals
            {where_sql}
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [approval for row in rows if (approval := get_policy_approval(str(row[0])))]


def update_policy_approval_status(
    approval_id: str,
    *,
    approval_status: str,
    approved_by: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    status = str(approval_status or "").strip()
    if status not in {"draft", "approved", "rejected", "rolled_out"}:
        raise ValueError("invalid_approval_status")
    now = int(time.time())
    with _connect() as c:
        row = c.execute(
            """
            SELECT policy_name, policy_version
            FROM policy_approvals
            WHERE approval_id=?
            """,
            (approval_id,),
        ).fetchone()
        if not row:
            raise KeyError("policy_approval_not_found")
        c.execute(
            """
            UPDATE policy_approvals
            SET approval_status=?, approved_by=COALESCE(?, approved_by),
                approved_ts=CASE WHEN ?='approved' THEN ? ELSE approved_ts END,
                notes=COALESCE(?, notes)
            WHERE approval_id=?
            """,
            (status, approved_by, status, now, notes, approval_id),
        )
        c.execute(
            """
            INSERT INTO policy_rollout_events (
                event_id, created_ts, rollout_id, approval_id, policy_name, policy_version,
                event_type, event_status, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                now,
                None,
                approval_id,
                row[0],
                row[1],
                "approval_status_changed",
                status,
                _json_dumps({"approved_by": approved_by, "notes": notes}),
            ),
        )
    return get_policy_approval(approval_id)  # type: ignore[return-value]


def list_policy_rollout_events(limit: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
    _ensure_schema()
    params: list[Any] = []
    where_sql = ""
    if event_type:
        where_sql = "WHERE event_type=?"
        params.append(event_type)
    params.append(max(1, limit))
    with _connect() as c:
        rows = c.execute(
            f"""
            SELECT event_id, created_ts, rollout_id, approval_id, policy_name, policy_version,
                   event_type, event_status, payload_json
            FROM policy_rollout_events
            {where_sql}
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [
        {
            "event_id": row[0],
            "created_ts": row[1],
            "rollout_id": row[2],
            "approval_id": row[3],
            "policy_name": row[4],
            "policy_version": row[5],
            "event_type": row[6],
            "event_status": row[7],
            "payload": json.loads(row[8] or "{}"),
        }
        for row in rows
    ]


def evaluate_policy_guardrails(
    *,
    hours: int = 24,
    min_samples: int = 3,
    max_negative_rate: float = 60.0,
    auto_apply: bool = False,
) -> dict[str, Any]:
    _ensure_schema()
    cutoff = int(time.time()) - max(1, hours) * 3600
    canaries = [
        rollout
        for rollout in list_policy_rollouts(limit=100, active_only=True)
        if rollout["rollout_mode"] == "canary"
    ]
    evaluations: list[dict[str, Any]] = []
    for rollout in canaries:
        with _connect() as c:
            rows = c.execute(
                """
                SELECT sd.decision_id, sd.signal_id, ss.outcome_label, sd.features_json
                FROM signal_decisions sd
                LEFT JOIN signal_snapshots ss
                  ON ss.signal_id = sd.signal_id
                 AND ss.horizon_minutes = (
                    SELECT MAX(horizon_minutes)
                    FROM signal_snapshots ss2
                    WHERE ss2.signal_id = sd.signal_id
                 )
                WHERE sd.policy_name=?
                  AND sd.policy_version=?
                  AND sd.created_ts >= ?
                  AND (? IS NULL OR sd.stage = ?)
                """,
                (
                    rollout["policy_name"],
                    rollout["policy_version"],
                    cutoff,
                    rollout["stage_scope"],
                    rollout["stage_scope"],
                ),
            ).fetchall()
        total = 0
        positive = 0
        negative = 0
        for row in rows:
            features: dict[str, Any] = {}
            try:
                features = json.loads(row[3] or "{}")
                if not isinstance(features, dict):
                    features = {}
            except Exception:
                features = {}
            if str(rollout.get("regime_scope") or "") and str(features.get("regime_key") or "") != str(rollout.get("regime_scope") or ""):
                continue
            outcome_label = str(row[2] or "pending")
            if outcome_label == "pending":
                continue
            total += 1
            if outcome_label in {"worked", "strong_continuation"}:
                positive += 1
            elif outcome_label in {"failed", "faded"}:
                negative += 1
        negative_rate = round((negative / total) * 100.0, 1) if total else 0.0
        recommended_action = "hold"
        applied = False
        if total >= max(1, min_samples) and negative_rate >= max_negative_rate:
            recommended_action = "rollback"
            if auto_apply:
                now = int(time.time())
                with _connect() as c:
                    c.execute(
                        "UPDATE policy_rollouts SET rollout_status='rolled_back' WHERE rollout_id=?",
                        (rollout["rollout_id"],),
                    )
                    c.execute(
                        """
                        INSERT INTO policy_rollout_events (
                            event_id, created_ts, rollout_id, approval_id, policy_name, policy_version,
                            event_type, event_status, payload_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid.uuid4().hex,
                            now,
                            rollout["rollout_id"],
                            None,
                            rollout["policy_name"],
                            rollout["policy_version"],
                            "guardrail_rollback",
                            "rolled_back",
                            _json_dumps(
                                {
                                    "hours": hours,
                                    "min_samples": min_samples,
                                    "max_negative_rate": max_negative_rate,
                                    "negative_rate": negative_rate,
                                    "total": total,
                                }
                            ),
                        ),
                    )
                applied = True
        evaluations.append(
            {
                "rollout_id": rollout["rollout_id"],
                "policy_name": rollout["policy_name"],
                "policy_version": rollout["policy_version"],
                "stage_scope": rollout["stage_scope"],
                "regime_scope": rollout.get("regime_scope"),
                "samples": total,
                "positive": positive,
                "negative": negative,
                "negative_rate": negative_rate,
                "recommended_action": recommended_action,
                "applied": applied,
            }
        )
    return {
        "hours": max(1, hours),
        "min_samples": max(1, min_samples),
        "max_negative_rate": max_negative_rate,
        "evaluations": evaluations,
    }


def list_policy_rollouts(limit: int = 20, active_only: bool = False) -> list[dict[str, Any]]:
    _ensure_schema()
    params: list[Any] = [max(1, limit)]
    where_sql = "WHERE rollout_status='active'" if active_only else ""
    with _connect() as c:
        rows = c.execute(
            f"""
            SELECT rollout_id, created_ts, policy_name, policy_version, rollout_mode, rollout_status,
                   stage_scope, regime_scope, traffic_percent, priority, activated_by, notes
            FROM policy_rollouts
            {where_sql}
            ORDER BY priority ASC, created_ts DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [
        {
            "rollout_id": row[0],
            "created_ts": row[1],
            "policy_name": row[2],
            "policy_version": row[3],
            "rollout_mode": row[4],
            "rollout_status": row[5],
            "stage_scope": row[6],
            "regime_scope": row[7],
            "traffic_percent": row[8],
            "priority": row[9],
            "activated_by": row[10],
            "notes": row[11],
        }
        for row in rows
    ]


def resolve_live_policy(stage: str, token: str | None = None, regime_key: str | None = None) -> dict[str, Any]:
    _ensure_schema()
    stage_value = str(stage or "").strip() or None
    regime_value = str(regime_key or "").strip() or None
    with _connect() as c:
        rows = c.execute(
            """
            SELECT r.rollout_id, r.policy_name, r.policy_version, r.rollout_mode, r.stage_scope,
                   r.regime_scope, r.traffic_percent, r.priority, p.config_json
            FROM policy_rollouts r
            JOIN policy_profiles p
              ON p.policy_name = r.policy_name
             AND p.policy_version = r.policy_version
            WHERE r.rollout_status='active'
            ORDER BY r.priority ASC, r.created_ts DESC
            """
        ).fetchall()
    selected_rollout = None
    fallback_active = None
    token_bucket = _policy_fingerprint(token)
    for row in rows:
        stage_scope = row[4]
        if stage_scope and stage_scope != stage_value:
            continue
        rollout_regime = str(row[5] or "").strip() or None
        if rollout_regime and rollout_regime != regime_value:
            continue
        rollout_mode = row[3]
        traffic_percent = int(row[6] or 0)
        if rollout_mode == "shadow":
            continue
        if fallback_active is None and rollout_mode == "active":
            fallback_active = row
        if rollout_mode == "canary" and token_bucket >= traffic_percent:
            continue
        selected_rollout = row
        break
    row = selected_rollout or fallback_active
    if row is None:
        descriptor = _default_policy_descriptor()
        return {
            "source": "default",
            "rollout_id": None,
            "policy_name": descriptor["policy_name"],
            "policy_version": descriptor["policy_version"],
            "rollout_mode": "default",
            "stage_scope": stage_value,
            "regime_scope": regime_value,
            "traffic_percent": 100,
            "config": descriptor,
        }
    config = json.loads(row[8] or "{}")
    if not isinstance(config, dict):
        config = {}
    resolved = _normalize_policy_descriptor(
        policy_name=str(row[1]),
        policy_version=str(row[2]),
        overrides=config,
    )
    return {
        "source": "rollout",
        "rollout_id": row[0],
        "policy_name": row[1],
        "policy_version": row[2],
        "rollout_mode": row[3],
        "stage_scope": row[4],
        "regime_scope": row[5],
        "traffic_percent": row[6],
        "config": resolved,
    }


def _normalize_feature_map(features: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(features or {})
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized[key] = value
        elif isinstance(value, (list, dict)):
            normalized[key] = value
        else:
            normalized[key] = str(value)
    return normalized


def classify_policy_regime(
    features: dict[str, Any] | None,
    *,
    stage: str | None = None,
    ts_value: float | None = None,
) -> dict[str, str]:
    feature_map = _normalize_feature_map(features)
    session = str(feature_map.get("session_bucket") or "").strip()
    if not session and ts_value is not None:
        session = str(_classify_time_features(ts_value).get("session_bucket") or "").strip()
    if not session:
        session = "unknown_session"

    liquidity = _to_float(feature_map.get("liquidity_usd"))
    if liquidity is None:
        liquidity_regime = "unknown_liquidity"
    elif liquidity < 10_000:
        liquidity_regime = "thin"
    elif liquidity < 50_000:
        liquidity_regime = "mid"
    else:
        liquidity_regime = "deep"

    age_minutes = _to_float(feature_map.get("age_minutes"))
    if age_minutes is None:
        age_regime = "unknown_age"
    elif age_minutes < 15:
        age_regime = "new"
    elif age_minutes < 180:
        age_regime = "developing"
    else:
        age_regime = "mature"

    price_change_m5 = _to_float(feature_map.get("price_change_m5"))
    price_change_h1 = _to_float(feature_map.get("price_change_h1"))
    buyers_15m = _to_int(feature_map.get("unique_buyers_15m"))
    if price_change_m5 is None and price_change_h1 is None:
        momentum_regime = "unknown_momentum"
    elif (price_change_m5 or 0.0) <= -10.0 or (price_change_h1 or 0.0) <= -20.0:
        momentum_regime = "reversing"
    elif (price_change_m5 or 0.0) >= 40.0 or (price_change_h1 or 0.0) >= 120.0:
        momentum_regime = "explosive"
    elif (price_change_m5 or 0.0) >= 10.0 or (price_change_h1 or 0.0) >= 30.0 or (buyers_15m or 0) >= 40:
        momentum_regime = "building"
    else:
        momentum_regime = "flat"

    stage_value = str(stage or feature_map.get("stage") or "").strip() or "unknown_stage"
    regime_key = "|".join(
        [stage_value, session, liquidity_regime, age_regime, momentum_regime]
    )
    return {
        "stage_regime": stage_value,
        "session_regime": session,
        "liquidity_regime": liquidity_regime,
        "age_regime": age_regime,
        "momentum_regime": momentum_regime,
        "regime_key": regime_key,
    }


def _derive_shadow_action(stage: str, features: dict[str, Any], policy: dict[str, Any]) -> str:
    if stage == "candidate":
        attention_score = _to_float(features.get("attention_score"))
        creator_score = _to_float(features.get("creator_score"))
        rate_limit_allowed = bool(features.get("candidate_rate_limit_allowed", True))
        progression_ok = bool(features.get("candidate_progression_ok", True))
        send_eligible = bool(features.get("candidate_send_eligible", True))
        if (
            attention_score is not None
            and creator_score is not None
            and attention_score >= float(policy["candidate_attention_min"])
            and creator_score >= float(policy["candidate_creator_min"])
            and rate_limit_allowed
            and progression_ok
            and send_eligible
        ):
            return "emit"
        return "hold"

    if stage == "promoted":
        confidence_score = _to_float(features.get("confidence_score"))
        attention_score = _to_float(features.get("attention_score"))
        risk_score = _to_float(features.get("risk_score"))
        liquidity_usd = _to_float(features.get("liquidity_usd"))
        buyers_15m = _to_int(features.get("unique_buyers_15m"))
        has_dex_pool = bool(features.get("has_dex_pool", True))
        lp_drain = bool(features.get("lp_drain", False))
        creator_sell = bool(features.get("creator_sell", False))
        if (
            confidence_score is not None
            and attention_score is not None
            and risk_score is not None
            and liquidity_usd is not None
            and buyers_15m is not None
            and has_dex_pool
            and not lp_drain
            and not creator_sell
            and confidence_score >= float(policy["promoted_confidence_min"])
            and attention_score >= float(policy["promoted_attention_min"])
            and risk_score <= float(policy["promoted_risk_max"])
            and liquidity_usd >= float(policy["promoted_liquidity_min"])
            and buyers_15m >= int(policy["promoted_buyers_15m_min"])
        ):
            return "emit"
        return "hold"

    return "observe"


def _outcome_label(snapshot: dict[str, Any], baseline: dict[str, Any]) -> str:
    mc_change = _percent_change(snapshot.get("market_cap_usd"), baseline.get("market_cap_usd"))
    liq_change = _percent_change(snapshot.get("liquidity_usd"), baseline.get("liquidity_usd"))
    if mc_change is None:
        return "insufficient_data"
    if mc_change >= 60 and (liq_change is None or liq_change >= -10):
        return "strong_continuation"
    if mc_change >= 20:
        return "worked"
    if mc_change <= -30 or (liq_change is not None and liq_change <= -40):
        return "failed"
    if mc_change < 0:
        return "faded"
    return "mixed"


def _ensure_signal_jobs(c: sqlite3.Connection, signal_id: str, ts_value: int) -> None:
    for horizon in SNAPSHOT_HORIZONS_MINUTES:
        c.execute(
            """
            INSERT INTO signal_snapshot_jobs (signal_id, horizon_minutes, due_ts, status)
            VALUES (?, ?, ?, 'pending')
            ON CONFLICT(signal_id, horizon_minutes) DO NOTHING
            """,
            (signal_id, horizon, ts_value + (horizon * 60)),
        )


def _ensure_signal_shell(
    *,
    token: str | None,
    event_type: str,
    ts_value: float,
    signal_id: str | None = None,
    source: str | None = None,
    creator: str | None = None,
    lifecycle: str | None = None,
    confidence_score: float | None = None,
    attention_score: float | None = None,
    risk_score: float | None = None,
) -> str | None:
    if not token:
        return None
    ts_int = int(ts_value)
    now = int(time.time())
    time_features = _classify_time_features(ts_value)
    with _connect() as c:
        existing_signal_id = signal_id
        if existing_signal_id:
            row = c.execute("SELECT signal_id FROM signals WHERE signal_id=?", (existing_signal_id,)).fetchone()
            if row is None:
                existing_signal_id = None
        if existing_signal_id is None:
            row = c.execute(
                """
                SELECT signal_id
                FROM signals
                WHERE token=? AND event_type=? AND ABS(alert_ts - ?) <= 900
                ORDER BY ABS(alert_ts - ?) ASC, updated_ts DESC
                LIMIT 1
                """,
                (token, event_type, ts_int, ts_int),
            ).fetchone()
            if row:
                existing_signal_id = str(row[0])

        resolved_signal_id = existing_signal_id or uuid.uuid4().hex
        if existing_signal_id:
            c.execute(
                """
                UPDATE signals
                SET updated_ts=?,
                    source=COALESCE(?, source),
                    creator=COALESCE(?, creator),
                    lifecycle=COALESCE(?, lifecycle),
                    confidence_score=COALESCE(?, confidence_score),
                    attention_score=COALESCE(?, attention_score),
                    risk_score=COALESCE(?, risk_score)
                WHERE signal_id=?
                """,
                (
                    now,
                    source,
                    creator,
                    lifecycle,
                    confidence_score,
                    attention_score,
                    risk_score,
                    resolved_signal_id,
                ),
            )
        else:
            c.execute(
                """
                INSERT INTO signals (
                    signal_id, external_ref, token, event_type, source, creator, alert_ts, updated_ts,
                    lifecycle, confidence_score, attention_score, risk_score, elite_score,
                    market_cap_usd, liquidity_usd, volume_m5_usd, age_minutes, price_change_m5,
                    price_change_h1, txns_m5_buys, txns_m5_sells,
                    hour_utc, day_of_week_utc, is_weekend_utc, hour_local, day_of_week_local,
                    local_daypart, session_bucket, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_signal_id,
                    None,
                    token,
                    event_type,
                    source,
                    creator,
                    ts_int,
                    now,
                    lifecycle,
                    confidence_score,
                    attention_score,
                    risk_score,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    time_features["hour_utc"],
                    time_features["day_of_week_utc"],
                    time_features["is_weekend_utc"],
                    time_features["hour_local"],
                    time_features["day_of_week_local"],
                    time_features["local_daypart"],
                    time_features["session_bucket"],
                    "{}",
                ),
            )
        _ensure_signal_jobs(c, resolved_signal_id, ts_int)
    return resolved_signal_id


def _persist_signal_event(event, *, external_ref: str | None = None, edited: bool = False) -> str:
    now = int(time.time())
    extra = event.extra if isinstance(event.extra, dict) else {}
    metrics = _extract_extra_metrics(extra)
    time_features = _classify_time_features(event.ts)
    payload_json = json.dumps(extra, sort_keys=True)
    existing_signal_id = str(extra.get("_signal_id") or "").strip() or None

    with _connect() as c:
        if existing_signal_id:
            row = c.execute("SELECT signal_id FROM signals WHERE signal_id=?", (existing_signal_id,)).fetchone()
            if row is None:
                existing_signal_id = None
        if existing_signal_id is None and external_ref:
            row = c.execute(
                "SELECT signal_id FROM signals WHERE external_ref=? AND event_type=? ORDER BY alert_ts DESC LIMIT 1",
                (external_ref, event.type),
            ).fetchone()
            if row:
                existing_signal_id = row[0]

        signal_id = existing_signal_id or uuid.uuid4().hex
        if existing_signal_id:
            c.execute(
                """
                UPDATE signals
                SET external_ref=COALESCE(?, external_ref),
                    updated_ts=?, confidence_score=?, attention_score=?, risk_score=?, elite_score=?,
                    market_cap_usd=?, liquidity_usd=?, volume_m5_usd=?, age_minutes=?, price_change_m5=?,
                    price_change_h1=?, txns_m5_buys=?, txns_m5_sells=?, lifecycle=?, payload_json=?
                WHERE signal_id=?
                """,
                (
                    external_ref,
                    now,
                    _to_float(event.confidence),
                    metrics["attention_score"],
                    metrics["risk_score"],
                    metrics["elite_score"],
                    metrics["market_cap_usd"],
                    metrics["liquidity_usd"],
                    metrics["volume_m5_usd"],
                    metrics["age_minutes"],
                    metrics["price_change_m5"],
                    metrics["price_change_h1"],
                    metrics["txns_m5_buys"],
                    metrics["txns_m5_sells"],
                    metrics["lifecycle"],
                    payload_json,
                    signal_id,
                ),
            )
        else:
            c.execute(
                """
                INSERT INTO signals (
                    signal_id, external_ref, token, event_type, source, creator, alert_ts, updated_ts,
                    lifecycle, confidence_score, attention_score, risk_score, elite_score,
                    market_cap_usd, liquidity_usd, volume_m5_usd, age_minutes, price_change_m5,
                    price_change_h1, txns_m5_buys, txns_m5_sells,
                    hour_utc, day_of_week_utc, is_weekend_utc, hour_local, day_of_week_local,
                    local_daypart, session_bucket, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    external_ref,
                    event.token,
                    event.type,
                    event.source,
                    event.creator,
                    int(event.ts),
                    now,
                    metrics["lifecycle"],
                    _to_float(event.confidence),
                    metrics["attention_score"],
                    metrics["risk_score"],
                    metrics["elite_score"],
                    metrics["market_cap_usd"],
                    metrics["liquidity_usd"],
                    metrics["volume_m5_usd"],
                    metrics["age_minutes"],
                    metrics["price_change_m5"],
                    metrics["price_change_h1"],
                    metrics["txns_m5_buys"],
                    metrics["txns_m5_sells"],
                    time_features["hour_utc"],
                    time_features["day_of_week_utc"],
                    time_features["is_weekend_utc"],
                    time_features["hour_local"],
                    time_features["day_of_week_local"],
                    time_features["local_daypart"],
                    time_features["session_bucket"],
                    payload_json,
                ),
            )

        _ensure_signal_jobs(c, signal_id, int(event.ts))

    logger.info(
        "[signal-learning] signal_recorded signal_id=%s token=%s type=%s edited=%s external_ref=%s",
        signal_id,
        event.token,
        event.type,
        edited,
        external_ref or "",
    )
    return signal_id


def _signal_event_payload(event, *, external_ref: str | None = None, edited: bool = False) -> dict[str, Any]:
    extra = event.extra if isinstance(event.extra, dict) else {}
    return {
        "event": {
            "type": event.type,
            "source": event.source,
            "token": event.token,
            "creator": event.creator,
            "confidence": event.confidence,
            "reasons": list(event.reasons) if isinstance(event.reasons, list) else [],
            "ts": event.ts,
            "extra": extra,
            "signature": event.signature,
        },
        "external_ref": external_ref,
        "edited": edited,
    }


def record_signal_event(event, *, external_ref: str | None = None, edited: bool = False) -> str:
    mode = _learning_write_mode()
    payload = _signal_event_payload(event, external_ref=external_ref, edited=edited)
    if mode in {"remote", "mirror"}:
        result = _post_internal_learning_write("/learning/internal/signals", payload)
        if result and result.get("signal_id"):
            return str(result["signal_id"])
        if mode == "remote":
            log_event(
                logger,
                logging.WARNING,
                "signal-learning-fallback",
                record_type="signal",
                mode=mode,
                role=_learning_process_role(),
                base_url=_learning_write_base_url(),
                token=getattr(event, "token", None),
                event_type=getattr(event, "type", None),
                fallback="local_persistence",
            )
    return _persist_signal_event(event, external_ref=external_ref, edited=edited)


def _persist_signal_decision(
    *,
    token: str | None,
    event_type: str,
    stage: str,
    decision: str,
    action_taken: str | None = None,
    reasons: list[str] | None = None,
    features: dict[str, Any] | None = None,
    policy_name: str | None = None,
    policy_version: str | None = None,
    attention_score: float | None = None,
    risk_score: float | None = None,
    confidence_score: float | None = None,
    creator_score: float | None = None,
    lifecycle: str | None = None,
    ts_value: float | None = None,
    signal_id: str | None = None,
    source: str | None = None,
    creator: str | None = None,
) -> str | None:
    created_ts = int(ts_value or time.time())
    time_features = _classify_time_features(created_ts)
    decision_id = uuid.uuid4().hex
    feature_map = _normalize_feature_map(features)
    if "session_bucket" not in feature_map:
        feature_map["session_bucket"] = time_features["session_bucket"]
    if attention_score is not None and "attention_score" not in feature_map:
        feature_map["attention_score"] = attention_score
    if risk_score is not None and "risk_score" not in feature_map:
        feature_map["risk_score"] = risk_score
    if confidence_score is not None and "confidence_score" not in feature_map:
        feature_map["confidence_score"] = confidence_score
    if creator_score is not None and "creator_score" not in feature_map:
        feature_map["creator_score"] = creator_score
    if lifecycle and "lifecycle" not in feature_map:
        feature_map["lifecycle"] = lifecycle
    if token and "token" not in feature_map:
        feature_map["token"] = token
    feature_map["stage"] = stage
    feature_map.update(classify_policy_regime(feature_map, stage=stage, ts_value=created_ts))
    resolved_policy = _normalize_policy_descriptor(
        policy_name=policy_name,
        policy_version=policy_version,
    )
    if "policy_descriptor" not in feature_map:
        feature_map["policy_descriptor"] = dict(resolved_policy)
    if "parameter_fingerprint" not in feature_map:
        feature_map["parameter_fingerprint"] = _policy_config_fingerprint(resolved_policy)
    resolved_signal_id = _ensure_signal_shell(
        token=token,
        event_type=event_type,
        ts_value=created_ts,
        signal_id=signal_id,
        source=source or f"decision:{stage}",
        creator=creator,
        lifecycle=lifecycle,
        confidence_score=confidence_score,
        attention_score=attention_score,
        risk_score=risk_score,
    )
    with _connect() as c:
        c.execute(
            """
            INSERT INTO signal_decisions (
                decision_id, signal_id, token, event_type, stage, decision, action_taken,
                policy_name, policy_version, reasons_json, features_json,
                attention_score, risk_score, confidence_score, creator_score, lifecycle,
                hour_utc, day_of_week_utc, is_weekend_utc, hour_local, day_of_week_local,
                local_daypart, session_bucket, created_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                resolved_signal_id,
                token,
                event_type,
                stage,
                decision,
                action_taken,
                resolved_policy["policy_name"],
                resolved_policy["policy_version"],
                json.dumps(reasons or []),
                _json_dumps(feature_map),
                attention_score,
                risk_score,
                confidence_score,
                creator_score,
                lifecycle,
                time_features["hour_utc"],
                time_features["day_of_week_utc"],
                time_features["is_weekend_utc"],
                time_features["hour_local"],
                time_features["day_of_week_local"],
                time_features["local_daypart"],
                time_features["session_bucket"],
                created_ts,
            ),
        )
    return resolved_signal_id


def _signal_decision_payload(
    *,
    token: str | None,
    event_type: str,
    stage: str,
    decision: str,
    action_taken: str | None = None,
    reasons: list[str] | None = None,
    features: dict[str, Any] | None = None,
    policy_name: str | None = None,
    policy_version: str | None = None,
    attention_score: float | None = None,
    risk_score: float | None = None,
    confidence_score: float | None = None,
    creator_score: float | None = None,
    lifecycle: str | None = None,
    ts_value: float | None = None,
    signal_id: str | None = None,
    source: str | None = None,
    creator: str | None = None,
) -> dict[str, Any]:
    return {
        "token": token,
        "event_type": event_type,
        "stage": stage,
        "decision": decision,
        "action_taken": action_taken,
        "reasons": reasons or [],
        "features": features or {},
        "policy_name": policy_name,
        "policy_version": policy_version,
        "attention_score": attention_score,
        "risk_score": risk_score,
        "confidence_score": confidence_score,
        "creator_score": creator_score,
        "lifecycle": lifecycle,
        "ts_value": ts_value,
        "signal_id": signal_id,
        "source": source,
        "creator": creator,
    }


def record_signal_decision(
    *,
    token: str | None,
    event_type: str,
    stage: str,
    decision: str,
    action_taken: str | None = None,
    reasons: list[str] | None = None,
    features: dict[str, Any] | None = None,
    policy_name: str | None = None,
    policy_version: str | None = None,
    attention_score: float | None = None,
    risk_score: float | None = None,
    confidence_score: float | None = None,
    creator_score: float | None = None,
    lifecycle: str | None = None,
    ts_value: float | None = None,
    signal_id: str | None = None,
    source: str | None = None,
    creator: str | None = None,
) -> str | None:
    mode = _learning_write_mode()
    payload = _signal_decision_payload(
        token=token,
        event_type=event_type,
        stage=stage,
        decision=decision,
        action_taken=action_taken,
        reasons=reasons,
        features=features,
        policy_name=policy_name,
        policy_version=policy_version,
        attention_score=attention_score,
        risk_score=risk_score,
        confidence_score=confidence_score,
        creator_score=creator_score,
        lifecycle=lifecycle,
        ts_value=ts_value,
        signal_id=signal_id,
        source=source,
        creator=creator,
    )
    if mode in {"remote", "mirror"}:
        result = _post_internal_learning_write("/learning/internal/decisions", payload)
        if result and "signal_id" in result:
            return str(result["signal_id"]) if result.get("signal_id") else None
        if mode == "remote":
            log_event(
                logger,
                logging.WARNING,
                "signal-learning-fallback",
                record_type="decision",
                mode=mode,
                role=_learning_process_role(),
                base_url=_learning_write_base_url(),
                token=token,
                stage=stage,
                decision=decision,
                fallback="local_persistence",
            )
    return _persist_signal_decision(
        token=token,
        event_type=event_type,
        stage=stage,
        decision=decision,
        action_taken=action_taken,
        reasons=reasons,
        features=features,
        policy_name=policy_name,
        policy_version=policy_version,
        attention_score=attention_score,
        risk_score=risk_score,
        confidence_score=confidence_score,
        creator_score=creator_score,
        lifecycle=lifecycle,
        ts_value=ts_value,
        signal_id=signal_id,
        source=source,
        creator=creator,
    )


def ingest_signal_event(payload: dict[str, Any]) -> dict[str, Any]:
    event_payload = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    if not event_payload:
        raise ValueError("event_payload_required")

    class _EventProxy:
        def __init__(self, data: dict[str, Any]) -> None:
            self.type = str(data.get("type") or "")
            self.source = str(data.get("source") or "")
            self.token = str(data.get("token") or "") or None
            self.creator = str(data.get("creator") or "") or None
            self.confidence = _to_float(data.get("confidence")) or 0.0
            self.reasons = data.get("reasons") if isinstance(data.get("reasons"), list) else []
            self.ts = float(data.get("ts") or time.time())
            self.extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
            self.signature = str(data.get("signature") or "") or None

    event = _EventProxy(event_payload)
    if not event.type:
        raise ValueError("event_type_required")
    signal_id = _persist_signal_event(
        event,
        external_ref=str(payload.get("external_ref") or "") or None,
        edited=bool(payload.get("edited") or False),
    )
    return {"signal_id": signal_id}


def ingest_signal_decision(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = str(payload.get("event_type") or "")
    stage = str(payload.get("stage") or "")
    decision = str(payload.get("decision") or "")
    if not event_type:
        raise ValueError("event_type_required")
    if not stage:
        raise ValueError("stage_required")
    if not decision:
        raise ValueError("decision_required")
    signal_id = _persist_signal_decision(
        token=str(payload.get("token") or "") or None,
        event_type=event_type,
        stage=stage,
        decision=decision,
        action_taken=str(payload.get("action_taken") or "") or None,
        reasons=payload.get("reasons") if isinstance(payload.get("reasons"), list) else None,
        features=payload.get("features") if isinstance(payload.get("features"), dict) else None,
        policy_name=str(payload.get("policy_name") or "") or None,
        policy_version=str(payload.get("policy_version") or "") or None,
        attention_score=_to_float(payload.get("attention_score")),
        risk_score=_to_float(payload.get("risk_score")),
        confidence_score=_to_float(payload.get("confidence_score")),
        creator_score=_to_float(payload.get("creator_score")),
        lifecycle=str(payload.get("lifecycle") or "") or None,
        ts_value=_to_float(payload.get("ts_value")),
        signal_id=str(payload.get("signal_id") or "") or None,
        source=str(payload.get("source") or "") or None,
        creator=str(payload.get("creator") or "") or None,
    )
    return {"signal_id": signal_id}


def get_policy_trace_summary(
    *,
    hours: int = 24,
    limit: int = 50,
    stage: str | None = None,
    decision: str | None = None,
    regime_key: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    cutoff = int(time.time()) - max(1, hours) * 3600
    clauses = ["created_ts >= ?"]
    params: list[Any] = [cutoff]
    if stage:
        clauses.append("stage = ?")
        params.append(stage)
    if decision:
        clauses.append("decision = ?")
        params.append(decision)
    params.append(max(1, limit))

    with _connect() as c:
        rows = c.execute(
            f"""
            SELECT decision_id, signal_id, token, event_type, stage, decision, action_taken,
                   policy_name, policy_version, reasons_json, features_json, attention_score,
                   risk_score, confidence_score, creator_score, lifecycle, session_bucket, created_ts
            FROM signal_decisions
            WHERE {" AND ".join(clauses)}
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    traces: list[dict[str, Any]] = []
    counts_by_stage: dict[str, int] = {}
    counts_by_action: dict[str, int] = {}
    policy_versions: dict[str, int] = {}
    for row in rows:
        reasons: list[str] = []
        features: dict[str, Any] = {}
        try:
            parsed_reasons = json.loads(row[9] or "[]")
            if isinstance(parsed_reasons, list):
                reasons = [str(item) for item in parsed_reasons]
        except Exception:
            reasons = []
        try:
            parsed_features = json.loads(row[10] or "{}")
            if isinstance(parsed_features, dict):
                features = parsed_features
        except Exception:
            features = {}
        if regime_key and str(features.get("regime_key") or "") != str(regime_key):
            continue
        trace = {
            "decision_id": row[0],
            "signal_id": row[1],
            "token": row[2],
            "event_type": row[3],
            "stage": row[4],
            "decision": row[5],
            "action_taken": row[6],
            "policy_name": row[7],
            "policy_version": row[8],
            "reasons": reasons,
            "features": features,
            "attention_score": row[11],
            "risk_score": row[12],
            "confidence_score": row[13],
            "creator_score": row[14],
            "lifecycle": row[15],
            "session_bucket": row[16],
            "created_ts": row[17],
        }
        traces.append(trace)
        counts_by_stage[trace["stage"] or "unknown"] = counts_by_stage.get(trace["stage"] or "unknown", 0) + 1
        counts_by_action[trace["action_taken"] or "unknown"] = counts_by_action.get(trace["action_taken"] or "unknown", 0) + 1
        policy_key = f"{trace['policy_name']}@{trace['policy_version']}"
        policy_versions[policy_key] = policy_versions.get(policy_key, 0) + 1

    return {
        "hours": max(1, hours),
        "trace_count": len(traces),
        "counts_by_stage": counts_by_stage,
        "counts_by_action": counts_by_action,
        "policy_versions": policy_versions,
        "traces": traces,
    }


def get_policy_regime_summary(*, hours: int = 24, limit: int = 20) -> dict[str, Any]:
    _ensure_schema()
    cutoff = int(time.time()) - max(1, hours) * 3600
    with _connect() as c:
        rows = c.execute(
            """
            SELECT features_json, action_taken, stage
            FROM signal_decisions
            WHERE created_ts >= ?
            ORDER BY created_ts DESC
            """,
            (cutoff,),
        ).fetchall()
        outcomes = c.execute(
            """
            SELECT sd.features_json, sd.stage, ss.outcome_label
            FROM signal_decisions sd
            LEFT JOIN signal_snapshots ss
              ON ss.signal_id = sd.signal_id
             AND ss.horizon_minutes = (
                SELECT MAX(horizon_minutes)
                FROM signal_snapshots ss2
                WHERE ss2.signal_id = sd.signal_id
             )
            WHERE sd.created_ts >= ?
            """,
            (cutoff,),
        ).fetchall()

    by_regime: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            features = json.loads(row[0] or "{}")
        except Exception:
            features = {}
        if not isinstance(features, dict):
            features = {}
        regime_key = str(features.get("regime_key") or "unknown")
        summary = by_regime.setdefault(
            regime_key,
            {
                "regime_key": regime_key,
                "stage": str(row[2] or features.get("stage") or ""),
                "session_regime": str(features.get("session_regime") or ""),
                "liquidity_regime": str(features.get("liquidity_regime") or ""),
                "age_regime": str(features.get("age_regime") or ""),
                "momentum_regime": str(features.get("momentum_regime") or ""),
                "decision_count": 0,
                "emit_count": 0,
                "hold_count": 0,
                "positive_outcomes": 0,
                "negative_outcomes": 0,
            },
        )
        summary["decision_count"] += 1
        if row[1] == "emit":
            summary["emit_count"] += 1
        elif row[1] == "hold":
            summary["hold_count"] += 1
    for row in outcomes:
        try:
            features = json.loads(row[0] or "{}")
        except Exception:
            features = {}
        if not isinstance(features, dict):
            features = {}
        regime_key = str(features.get("regime_key") or "unknown")
        summary = by_regime.setdefault(
            regime_key,
            {
                "regime_key": regime_key,
                "stage": str(row[1] or features.get("stage") or ""),
                "session_regime": str(features.get("session_regime") or ""),
                "liquidity_regime": str(features.get("liquidity_regime") or ""),
                "age_regime": str(features.get("age_regime") or ""),
                "momentum_regime": str(features.get("momentum_regime") or ""),
                "decision_count": 0,
                "emit_count": 0,
                "hold_count": 0,
                "positive_outcomes": 0,
                "negative_outcomes": 0,
            },
        )
        outcome_label = str(row[2] or "pending")
        if outcome_label in {"worked", "strong_continuation"}:
            summary["positive_outcomes"] += 1
        elif outcome_label in {"failed", "faded"}:
            summary["negative_outcomes"] += 1
    regimes = sorted(
        by_regime.values(),
        key=lambda item: (int(item["decision_count"]), int(item["positive_outcomes"])),
        reverse=True,
    )[: max(1, limit)]
    return {"hours": max(1, hours), "regimes": regimes}


def evaluate_shadow_policy(
    *,
    hours: int = 24,
    limit: int = 200,
    stage: str | None = None,
    regime_key: str | None = None,
    policy_name: str | None = None,
    policy_version: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = get_policy_trace_summary(hours=hours, limit=limit, stage=stage, regime_key=regime_key)
    shadow_policy = _normalize_policy_descriptor(
        policy_name=policy_name or "shadow_policy",
        policy_version=policy_version or "shadow-v1",
        overrides=overrides,
    )
    cutoff = int(time.time()) - max(1, hours) * 3600
    with _connect() as c:
        outcome_rows = c.execute(
            """
            SELECT s.signal_id, ss.outcome_label, ss.market_cap_change_pct
            FROM signals s
            LEFT JOIN signal_snapshots ss
              ON ss.signal_id = s.signal_id
             AND ss.horizon_minutes = (
                SELECT MAX(horizon_minutes)
                FROM signal_snapshots ss2
                WHERE ss2.signal_id = s.signal_id
             )
            WHERE s.alert_ts >= ?
            """,
            (cutoff,),
        ).fetchall()
    outcome_by_signal_id = {
        str(row[0]): {
            "outcome_label": row[1] or "pending",
            "market_cap_change_pct": row[2],
        }
        for row in outcome_rows
        if row[0]
    }

    changed_examples: list[dict[str, Any]] = []
    changed_count = 0
    stage_changes: dict[str, int] = {}
    impact = {"positive_outcomes": 0, "negative_outcomes": 0, "pending_outcomes": 0}
    for trace in summary["traces"]:
        features = _normalize_feature_map(trace.get("features"))
        shadow_action = _derive_shadow_action(str(trace.get("stage") or ""), features, shadow_policy)
        current_action = str(trace.get("action_taken") or "unknown")
        if shadow_action == current_action:
            continue
        changed_count += 1
        stage_key = str(trace.get("stage") or "unknown")
        stage_changes[stage_key] = stage_changes.get(stage_key, 0) + 1
        outcome = outcome_by_signal_id.get(str(trace.get("signal_id") or ""))
        outcome_label = str((outcome or {}).get("outcome_label") or "pending")
        if outcome_label in {"worked", "strong_continuation"}:
            impact["positive_outcomes"] += 1
        elif outcome_label in {"failed", "faded"}:
            impact["negative_outcomes"] += 1
        else:
            impact["pending_outcomes"] += 1
        if len(changed_examples) < 25:
            changed_examples.append(
                {
                    "decision_id": trace["decision_id"],
                    "signal_id": trace["signal_id"],
                    "token": trace["token"],
                    "stage": trace["stage"],
                    "decision": trace["decision"],
                    "current_action": current_action,
                    "shadow_action": shadow_action,
                    "policy_version": trace["policy_version"],
                    "shadow_policy_version": shadow_policy["policy_version"],
                    "attention_score": trace["attention_score"],
                    "risk_score": trace["risk_score"],
                    "confidence_score": trace["confidence_score"],
                    "creator_score": trace["creator_score"],
                    "features": features,
                    "outcome_label": outcome_label,
                    "market_cap_change_pct": (outcome or {}).get("market_cap_change_pct"),
                }
            )

    trace_count = int(summary["trace_count"] or 0)
    return {
        "hours": max(1, hours),
        "trace_count": trace_count,
        "changed_count": changed_count,
        "change_rate": round((changed_count / trace_count) * 100.0, 1) if trace_count else 0.0,
        "regime_key": regime_key,
        "stage_changes": stage_changes,
        "current_policy_versions": summary["policy_versions"],
        "shadow_policy": shadow_policy,
        "impact": impact,
        "changed_examples": changed_examples,
    }


def run_policy_replay(
    *,
    hours: int = 24,
    limit: int = 500,
    stage: str | None = None,
    regime_key: str | None = None,
    policy_name: str | None = None,
    policy_version: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = get_policy_trace_summary(hours=hours, limit=limit, stage=stage, regime_key=regime_key)
    shadow_summary = evaluate_shadow_policy(
        hours=hours,
        limit=limit,
        stage=stage,
        regime_key=regime_key,
        policy_name=policy_name,
        policy_version=policy_version,
        overrides=overrides,
    )
    shadow_policy = shadow_summary["shadow_policy"]
    run_id = uuid.uuid4().hex
    created_ts = int(time.time())

    current_policy_versions = summary.get("policy_versions") if isinstance(summary.get("policy_versions"), dict) else {}
    baseline_policy_name = None
    baseline_policy_version = None
    if current_policy_versions:
        top_key = max(current_policy_versions.items(), key=lambda item: int(item[1] or 0))[0]
        if "@" in top_key:
            baseline_policy_name, baseline_policy_version = top_key.split("@", 1)

    changed_examples_by_id = {
        str(item.get("decision_id")): item
        for item in shadow_summary.get("changed_examples", [])
        if item.get("decision_id")
    }
    replay_results: list[tuple[Any, ...]] = []
    for trace in summary["traces"]:
        decision_id = str(trace.get("decision_id") or "")
        changed_example = changed_examples_by_id.get(decision_id)
        shadow_action = (
            str(changed_example.get("shadow_action"))
            if changed_example is not None
            else _derive_shadow_action(
                str(trace.get("stage") or ""),
                _normalize_feature_map(trace.get("features")),
                shadow_policy,
            )
        )
        current_action = str(trace.get("action_taken") or "unknown")
        changed = 1 if shadow_action != current_action else 0
        replay_results.append(
            (
                run_id,
                decision_id,
                trace.get("signal_id"),
                trace.get("token"),
                trace.get("stage"),
                current_action,
                shadow_action,
                changed,
                changed_example.get("outcome_label") if changed_example else None,
                changed_example.get("market_cap_change_pct") if changed_example else None,
                _json_dumps(_normalize_feature_map(trace.get("features"))),
            )
        )

    replay_summary = {
        "run_id": run_id,
        "created_ts": created_ts,
        "hours": max(1, hours),
        "trace_limit": max(1, limit),
        "stage": stage,
        "regime_key": regime_key,
        "baseline_policy_name": baseline_policy_name,
        "baseline_policy_version": baseline_policy_version,
        "shadow_policy": shadow_policy,
        "trace_count": shadow_summary["trace_count"],
        "changed_count": shadow_summary["changed_count"],
        "change_rate": shadow_summary["change_rate"],
        "stage_changes": shadow_summary["stage_changes"],
        "impact": shadow_summary["impact"],
        "changed_examples": shadow_summary["changed_examples"],
    }

    with _connect() as c:
        c.execute(
            """
            INSERT INTO policy_replay_runs (
                run_id, created_ts, hours, trace_limit, stage,
                baseline_policy_name, baseline_policy_version,
                shadow_policy_name, shadow_policy_version,
                overrides_json, summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                created_ts,
                max(1, hours),
                max(1, limit),
                stage,
                baseline_policy_name,
                baseline_policy_version,
                shadow_policy["policy_name"],
                shadow_policy["policy_version"],
                _json_dumps(overrides or {}),
                _json_dumps(replay_summary),
            ),
        )
        c.executemany(
            """
            INSERT INTO policy_replay_results (
                run_id, decision_id, signal_id, token, stage, current_action,
                shadow_action, changed, outcome_label, market_cap_change_pct, features_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            replay_results,
        )

    return replay_summary


def get_policy_replay(run_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    with _connect() as c:
        run_row = c.execute(
            """
            SELECT summary_json
            FROM policy_replay_runs
            WHERE run_id=?
            """,
            (run_id,),
        ).fetchone()
        result_rows = c.execute(
            """
            SELECT decision_id, signal_id, token, stage, current_action, shadow_action,
                   changed, outcome_label, market_cap_change_pct, features_json
            FROM policy_replay_results
            WHERE run_id=?
            ORDER BY changed DESC, token ASC
            """,
            (run_id,),
        ).fetchall()
    if not run_row or not run_row[0]:
        return None
    summary = json.loads(run_row[0])
    results: list[dict[str, Any]] = []
    for row in result_rows:
        try:
            features = json.loads(row[9] or "{}")
            if not isinstance(features, dict):
                features = {}
        except Exception:
            features = {}
        results.append(
            {
                "decision_id": row[0],
                "signal_id": row[1],
                "token": row[2],
                "stage": row[3],
                "current_action": row[4],
                "shadow_action": row[5],
                "changed": bool(row[6]),
                "outcome_label": row[7],
                "market_cap_change_pct": row[8],
                "features": features,
            }
        )
    summary["results"] = results
    return summary


def get_latest_policy_replay() -> dict[str, Any] | None:
    _ensure_schema()
    with _connect() as c:
        row = c.execute(
            """
            SELECT run_id
            FROM policy_replay_runs
            ORDER BY created_ts DESC
            LIMIT 1
            """
        ).fetchone()
    if not row or not row[0]:
        return None
    return get_policy_replay(str(row[0]))


def list_policy_replays(limit: int = 20) -> list[dict[str, Any]]:
    _ensure_schema()
    with _connect() as c:
        rows = c.execute(
            """
            SELECT summary_json
            FROM policy_replay_runs
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
    replays: list[dict[str, Any]] = []
    for row in rows:
        if not row or not row[0]:
            continue
        try:
            payload = json.loads(row[0] or "{}")
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            replays.append(payload)
    return replays


def _policy_automation_config() -> dict[str, Any]:
    return {
        "replay_limit": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTOMATION_REPLAY_LIMIT", "20"))),
        "generation_limit": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_GENERATION_LIMIT", "6"))),
        "generation_replay_limit": max(25, int(os.getenv("SIGNAL_ENGINE_POLICY_GENERATION_REPLAY_LIMIT", "250"))),
        "generation_novelty_min": float(os.getenv("SIGNAL_ENGINE_POLICY_GENERATION_NOVELTY_MIN", "0.05")),
        "generation_proposal_score_min": float(os.getenv("SIGNAL_ENGINE_POLICY_GENERATION_PROPOSAL_SCORE_MIN", "45.0")),
        "auto_approval_min_trace_count": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_APPROVAL_MIN_TRACE_COUNT", "1"))),
        "auto_approval_min_changed_count": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_APPROVAL_MIN_CHANGED_COUNT", "1"))),
        "auto_approval_min_change_rate": float(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_APPROVAL_MIN_CHANGE_RATE", "1.0")),
        "auto_approval_max_negative_rate": float(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_APPROVAL_MAX_NEGATIVE_RATE", "35.0")),
        "auto_approval_min_positive_outcomes": max(0, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_APPROVAL_MIN_POSITIVE_OUTCOMES", "0"))),
        "auto_approval_cooldown_sec": max(0, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_APPROVAL_COOLDOWN_SEC", "1800"))),
        "canary_cooldown_sec": max(0, int(os.getenv("SIGNAL_ENGINE_POLICY_CANARY_COOLDOWN_SEC", "1800"))),
        "promotion_cooldown_sec": max(0, int(os.getenv("SIGNAL_ENGINE_POLICY_PROMOTION_COOLDOWN_SEC", "3600"))),
        "canary_traffic_percent": max(1, min(100, int(os.getenv("SIGNAL_ENGINE_POLICY_CANARY_TRAFFIC_PERCENT", "10")))),
        "canary_priority": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_CANARY_PRIORITY", "5"))),
        "max_auto_approvals_per_day": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_MAX_AUTO_APPROVALS_PER_DAY", "10"))),
        "max_canary_rollouts_per_day": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_MAX_CANARY_ROLLOUTS_PER_DAY", "4"))),
        "max_promotions_per_day": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_MAX_PROMOTIONS_PER_DAY", "3"))),
        "max_regime_actions_per_day": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_MAX_REGIME_ACTIONS_PER_DAY", "2"))),
        "max_active_canaries_per_stage": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_MAX_ACTIVE_CANARIES_PER_STAGE", "1"))),
        "max_total_canary_traffic_percent": max(1, min(100, int(os.getenv("SIGNAL_ENGINE_POLICY_MAX_TOTAL_CANARY_TRAFFIC_PERCENT", "35")))),
        "max_high_risk_active_regimes": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_MAX_HIGH_RISK_ACTIVE_REGIMES", "1"))),
        "duplicate_profile_lookback_hours": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_DUPLICATE_PROFILE_LOOKBACK_HOURS", "168"))),
        "regime_action_cooldown_sec": max(0, int(os.getenv("SIGNAL_ENGINE_POLICY_REGIME_ACTION_COOLDOWN_SEC", "3600"))),
        "auto_promote_min_samples": max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_PROMOTE_MIN_SAMPLES", "3"))),
        "auto_promote_max_negative_rate": float(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_PROMOTE_MAX_NEGATIVE_RATE", "34.0")),
        "auto_promote_min_positive_rate": float(os.getenv("SIGNAL_ENGINE_POLICY_AUTO_PROMOTE_MIN_POSITIVE_RATE", "50.0")),
    }


def get_policy_automation_status() -> dict[str, Any]:
    config = _policy_automation_config()
    active_rollouts = list_policy_rollouts(limit=50, active_only=True)
    recent_runs = list_policy_automation_runs(limit=5)
    return {
        "config": config,
        "recent_replays": list_policy_replays(limit=min(10, int(config["replay_limit"]))),
        "latest_run": get_latest_policy_automation_run(),
        "recent_runs": recent_runs,
        "guardrails": _policy_automation_guardrails(),
        "regime_feedback": get_regime_action_feedback(hours=168),
        "regime_meta_policy": get_regime_meta_policy(hours=168),
        "pending_approvals": [
            item
            for item in list_policy_approvals(limit=20)
            if str(item.get("approval_status") or "") in {"draft", "approved"}
        ],
        "active_rollouts": active_rollouts,
        "active_canaries": [item for item in active_rollouts if item.get("rollout_mode") == "canary"],
    }


def _find_policy_profile(policy_name: str, policy_version: str) -> dict[str, Any] | None:
    for item in list_policy_profiles(limit=200, policy_name=policy_name):
        if str(item.get("policy_version") or "") == str(policy_version or ""):
            return item
    return None


def _recent_policy_event_count(event_types: set[str], window_seconds: int) -> int:
    if not event_types:
        return 0
    cutoff = int(time.time()) - max(1, window_seconds)
    placeholders = ", ".join("?" for _ in event_types)
    with _connect() as c:
        row = c.execute(
            f"""
            SELECT COUNT(1)
            FROM policy_rollout_events
            WHERE created_ts >= ?
              AND event_type IN ({placeholders})
            """,
            (cutoff, *sorted(event_types)),
        ).fetchone()
    return int((row[0] if row else 0) or 0)


def _latest_policy_event_ts(
    *,
    event_types: set[str],
    policy_name: str | None = None,
    policy_version: str | None = None,
    stage_scope: str | None = None,
    regime_scope: str | None = None,
) -> int | None:
    if not event_types:
        return None
    with _connect() as c:
        rows = c.execute(
            f"""
            SELECT created_ts, policy_name, policy_version, payload_json
            FROM policy_rollout_events
            WHERE event_type IN ({', '.join('?' for _ in event_types)})
            ORDER BY created_ts DESC
            LIMIT 100
            """,
            tuple(sorted(event_types)),
        ).fetchall()
    for row in rows:
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(row[3] or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        if policy_name and str(row[1] or "") != policy_name:
            continue
        if policy_version and str(row[2] or "") != policy_version:
            continue
        if stage_scope is not None and str(payload.get("stage_scope") or "") != str(stage_scope or ""):
            continue
        if regime_scope is not None and str(payload.get("regime_scope") or "") != str(regime_scope or ""):
            continue
        return int(row[0] or 0) or None
    return None


def _cooldown_remaining(last_ts: int | None, cooldown_seconds: int) -> int:
    if not last_ts or cooldown_seconds <= 0:
        return 0
    remaining = (int(last_ts) + int(cooldown_seconds)) - int(time.time())
    return max(0, remaining)


def _active_canary_count(stage_scope: str | None, regime_scope: str | None = None) -> int:
    return len(
        [
            item
            for item in list_policy_rollouts(limit=200, active_only=True)
            if item.get("rollout_mode") == "canary"
            and (item.get("stage_scope") or None) == stage_scope
            and (item.get("regime_scope") or None) == regime_scope
        ]
    )


def _equivalent_profile_exists(policy_name: str, policy_version: str, lookback_hours: int) -> bool:
    target = _find_policy_profile(policy_name, policy_version)
    if target is None:
        return False
    target_fingerprint = _policy_config_fingerprint(target.get("config") if isinstance(target.get("config"), dict) else {})
    cutoff = int(time.time()) - max(1, lookback_hours) * 3600
    for profile in list_policy_profiles(limit=500):
        if profile.get("policy_name") == policy_name and profile.get("policy_version") == policy_version:
            continue
        if int(profile.get("created_ts") or 0) < cutoff:
            continue
        fingerprint = _policy_config_fingerprint(profile.get("config") if isinstance(profile.get("config"), dict) else {})
        if fingerprint == target_fingerprint:
            return True
    return False


def _policy_automation_guardrails() -> dict[str, Any]:
    config = _policy_automation_config()
    return {
        "cooldowns": {
            "auto_approval_sec": int(config["auto_approval_cooldown_sec"]),
            "canary_sec": int(config["canary_cooldown_sec"]),
            "promotion_sec": int(config["promotion_cooldown_sec"]),
            "regime_action_sec": int(config["regime_action_cooldown_sec"]),
        },
        "budgets": {
            "auto_approvals_used": _recent_policy_event_count({"auto_approval_created"}, 86400),
            "auto_approvals_max": int(config["max_auto_approvals_per_day"]),
            "canaries_used": _recent_policy_event_count({"auto_canary_started"}, 86400),
            "canaries_max": int(config["max_canary_rollouts_per_day"]),
            "promotions_used": _recent_policy_event_count({"canary_promoted"}, 86400),
            "promotions_max": int(config["max_promotions_per_day"]),
            "regime_actions_used": _recent_policy_event_count({"auto_regime_action_started"}, 86400),
            "regime_actions_max": int(config["max_regime_actions_per_day"]),
        },
        "portfolio": get_policy_portfolio_budget(hours=168),
        "active_canaries_by_stage": {
            stage or "all": _active_canary_count(stage)
            for stage in {None, "candidate", "promoted"}
        },
    }


def _clamp_float(value: float, lower: float, upper: float, *, digits: int = 2) -> float:
    return round(min(max(value, lower), upper), digits)


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return int(min(max(int(value), lower), upper))


def _policy_generation_variants(
    *,
    hours: int,
    generation_limit: int,
) -> list[dict[str, Any]]:
    diagnostics = get_diagnostics_summary(hours=max(1, hours))
    regime_summary = get_policy_regime_summary(hours=max(1, hours), limit=max(4, generation_limit * 2))
    variants: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_candidate = resolve_live_policy("candidate")
    base_promoted = resolve_live_policy("promoted")
    candidate_config = dict(base_candidate.get("config") or {})
    promoted_config = dict(base_promoted.get("config") or {})
    false_negatives = diagnostics.get("false_negatives") if isinstance(diagnostics.get("false_negatives"), list) else []
    false_positives = diagnostics.get("false_positives") if isinstance(diagnostics.get("false_positives"), list) else []
    threshold_guidance = diagnostics.get("threshold_guidance") if isinstance(diagnostics.get("threshold_guidance"), list) else []

    positive_regimes = [
        item
        for item in (regime_summary.get("regimes") or [])
        if isinstance(item, dict) and int(item.get("positive_outcomes") or 0) > 0
    ]
    negative_regimes = [
        item
        for item in (regime_summary.get("regimes") or [])
        if isinstance(item, dict) and int(item.get("negative_outcomes") or 0) > 0
    ]

    def add_variant(
        stage: str,
        config: dict[str, Any],
        rationale: list[str],
        source_signal: str,
        regime_key: str | None = None,
    ) -> None:
        if len(variants) >= max(1, generation_limit):
            return
        fingerprint = f"{regime_key or 'global'}::{_policy_config_fingerprint(config)}"
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        variants.append(
            {
                "stage": stage,
                "regime_key": regime_key,
                "config": config,
                "rationale": rationale,
                "source_signal": source_signal,
                "config_fingerprint": fingerprint,
            }
        )

    if false_negatives:
        relaxed_candidate = _normalize_policy_descriptor(
            policy_name="generated_candidate_policy",
            policy_version="pending",
            overrides={
                "candidate_attention_min": _clamp_float(float(candidate_config.get("candidate_attention_min") or 0.70) - 0.05, 0.45, 0.90),
                "candidate_creator_min": _clamp_float(float(candidate_config.get("candidate_creator_min") or 0.30) - 0.05, 0.05, 0.80),
                "candidate_gate_attention_min": _clamp_float(float(candidate_config.get("candidate_gate_attention_min") or 0.14) - 0.02, 0.08, 0.35),
                "candidate_gate_min_age_sec": _clamp_int(int(candidate_config.get("candidate_gate_min_age_sec") or 15) - 5, 5, 60),
            },
        )
        add_variant(
            "candidate",
            relaxed_candidate,
            ["False negatives detected in candidate gating", "Relax early attention and creator thresholds slightly"],
            "false_negatives",
        )
        for regime in positive_regimes:
            if str(regime.get("stage") or "") != "candidate":
                continue
            regime_key = str(regime.get("regime_key") or "")
            if not regime_key:
                continue
            add_variant(
                "candidate",
                relaxed_candidate,
                [
                    f"False negatives clustered in regime {regime_key}",
                    "Relax candidate gating only for a productive regime",
                ],
                "regime_false_negatives",
                regime_key=regime_key,
            )
            break

    if false_positives:
        stricter_promoted = _normalize_policy_descriptor(
            policy_name="generated_promoted_policy",
            policy_version="pending",
            overrides={
                "promoted_risk_max": _clamp_float(float(promoted_config.get("promoted_risk_max") or 0.60) - 0.05, 0.20, 0.80),
                "promoted_liquidity_min": _clamp_float(float(promoted_config.get("promoted_liquidity_min") or 15000.0) + 2500.0, 5000.0, 100000.0, digits=1),
                "promoted_buyers_15m_min": _clamp_int(int(promoted_config.get("promoted_buyers_15m_min") or 30) + 5, 5, 200),
            },
        )
        add_variant(
            "promoted",
            stricter_promoted,
            ["False positives detected after promotion", "Tighten risk, liquidity, and buyer requirements"],
            "false_positives",
        )
        for regime in negative_regimes:
            if str(regime.get("stage") or "") != "promoted":
                continue
            regime_key = str(regime.get("regime_key") or "")
            if not regime_key:
                continue
            add_variant(
                "promoted",
                stricter_promoted,
                [
                    f"Negative outcomes concentrated in regime {regime_key}",
                    "Tighten promoted gating only where failure pressure is concentrated",
                ],
                "regime_false_positives",
                regime_key=regime_key,
            )
            break

    guidance_map = {str(item.get("reason") or ""): item for item in threshold_guidance if isinstance(item, dict)}
    attention_guidance = guidance_map.get("attention<0.20")
    if attention_guidance and str(attention_guidance.get("action") or "") == "relax_slightly":
        relaxed_attention = _normalize_policy_descriptor(
            policy_name="generated_candidate_policy",
            policy_version="pending",
            overrides={
                "candidate_attention_min": _clamp_float(float(candidate_config.get("candidate_attention_min") or 0.70) - 0.03, 0.45, 0.90),
                "candidate_gate_attention_min": _clamp_float(float(candidate_config.get("candidate_gate_attention_min") or 0.14) - 0.02, 0.08, 0.35),
            },
        )
        add_variant(
            "candidate",
            relaxed_attention,
            ["Historical guidance supports slight relaxation of candidate attention threshold"],
            "threshold_guidance",
        )

    liq_guidance = guidance_map.get("dex_gate:liq<12000.0")
    if liq_guidance and str(liq_guidance.get("action") or "") == "tighten":
        tighter_liquidity = _normalize_policy_descriptor(
            policy_name="generated_promoted_policy",
            policy_version="pending",
            overrides={
                "promoted_liquidity_min": _clamp_float(float(promoted_config.get("promoted_liquidity_min") or 15000.0) + 2000.0, 5000.0, 100000.0, digits=1),
            },
        )
        add_variant(
            "promoted",
            tighter_liquidity,
            ["Historical guidance supports tighter promoted liquidity floor"],
            "threshold_guidance",
        )

    return variants[: max(1, generation_limit)]


def _policy_stage_from_regime_key(regime_key: str | None, stage: str | None = None) -> str:
    if stage:
        stage_value = str(stage or "").strip()
        if stage_value:
            return stage_value
    raw = str(regime_key or "").strip()
    if raw and "|" in raw:
        return raw.split("|", 1)[0] or "candidate"
    return raw or "candidate"


def _regime_action_overrides(stage: str, action: str, base_config: dict[str, Any]) -> dict[str, Any]:
    if stage == "candidate":
        attention = float(base_config.get("candidate_attention_min") or 0.70)
        creator = float(base_config.get("candidate_creator_min") or 0.30)
        gate_attention = float(base_config.get("candidate_gate_attention_min") or 0.14)
        gate_age = int(base_config.get("candidate_gate_min_age_sec") or 15)
        if action in {"tighten", "canary_tighten"}:
            return {
                "candidate_attention_min": _clamp_float(attention + 0.03, 0.45, 0.90),
                "candidate_creator_min": _clamp_float(creator + 0.03, 0.05, 0.80),
                "candidate_gate_attention_min": _clamp_float(gate_attention + 0.02, 0.08, 0.35),
                "candidate_gate_min_age_sec": _clamp_int(gate_age + 5, 5, 60),
            }
        return {
            "candidate_attention_min": _clamp_float(attention - 0.03, 0.45, 0.90),
            "candidate_creator_min": _clamp_float(creator - 0.03, 0.05, 0.80),
            "candidate_gate_attention_min": _clamp_float(gate_attention - 0.02, 0.08, 0.35),
            "candidate_gate_min_age_sec": _clamp_int(gate_age - 5, 5, 60),
        }

    confidence = float(base_config.get("promoted_confidence_min") or 0.80)
    attention = float(base_config.get("promoted_attention_min") or 0.50)
    risk = float(base_config.get("promoted_risk_max") or 0.60)
    liquidity = float(base_config.get("promoted_liquidity_min") or 15000.0)
    buyers = int(base_config.get("promoted_buyers_15m_min") or 30)
    if action in {"tighten", "canary_tighten"}:
        return {
            "promoted_confidence_min": _clamp_float(confidence + 0.02, 0.50, 0.99),
            "promoted_attention_min": _clamp_float(attention + 0.03, 0.20, 0.95),
            "promoted_risk_max": _clamp_float(risk - 0.04, 0.20, 0.80),
            "promoted_liquidity_min": _clamp_float(liquidity + 2500.0, 5000.0, 100000.0, digits=1),
            "promoted_buyers_15m_min": _clamp_int(buyers + 4, 5, 200),
        }
    return {
        "promoted_confidence_min": _clamp_float(confidence - 0.02, 0.50, 0.99),
        "promoted_attention_min": _clamp_float(attention - 0.03, 0.20, 0.95),
        "promoted_risk_max": _clamp_float(risk + 0.04, 0.20, 0.80),
        "promoted_liquidity_min": _clamp_float(liquidity - 2500.0, 5000.0, 100000.0, digits=1),
        "promoted_buyers_15m_min": _clamp_int(buyers - 4, 5, 200),
    }


def execute_regime_policy_action(
    *,
    regime_key: str,
    action: str,
    stage: str | None = None,
    actor: str | None = None,
    hours: int = 24,
    replay_limit: int = 250,
    traffic_percent: int | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    regime_value = str(regime_key or "").strip()
    if not regime_value:
        raise ValueError("regime_key_required")
    action_value = str(action or "").strip()
    if action_value not in {"tighten", "relax", "canary_tighten", "canary_relax"}:
        raise ValueError("invalid_regime_action")
    stage_value = _policy_stage_from_regime_key(regime_value, stage)
    base_policy = resolve_live_policy(stage_value, regime_key=regime_value)
    base_config = dict(base_policy.get("config") or {})
    overrides = _regime_action_overrides(stage_value, action_value, base_config)
    ts_suffix = int(time.time())
    policy_name = f"manual_{stage_value}_policy"
    policy_version = f"{action_value}-{ts_suffix}-{uuid.uuid4().hex[:6]}"
    profile = create_policy_profile(
        policy_name=policy_name,
        policy_version=policy_version,
        config=overrides,
        description=f"Manual regime action {action_value} for {regime_value}",
        created_by=actor or "command-center",
    )
    replay = run_policy_replay(
        hours=max(1, hours),
        limit=max(25, replay_limit),
        stage=stage_value,
        regime_key=regime_value,
        policy_name=policy_name,
        policy_version=policy_version,
        overrides=overrides,
    )
    approval = create_policy_approval(
        policy_name=policy_name,
        policy_version=policy_version,
        source_type="replay",
        source_ref=str(replay.get("run_id") or ""),
        notes=f"Manual regime action {action_value} for {regime_value}",
        approved_by=actor or "command-center",
    )
    rollout = None
    if action_value.startswith("canary_"):
        rollout = activate_policy_rollout(
            policy_name=policy_name,
            policy_version=policy_version,
            rollout_mode="canary",
            rollout_status="active",
            stage_scope=stage_value,
            regime_scope=regime_value,
            traffic_percent=int(traffic_percent or _policy_automation_config()["canary_traffic_percent"]),
            priority=int(_policy_automation_config()["canary_priority"]),
            activated_by=actor or "command-center",
            notes=f"Manual regime canary {action_value} for {regime_value}",
        )
        update_policy_approval_status(
            str(approval.get("approval_id") or ""),
            approval_status="rolled_out",
            approved_by=actor or "command-center",
            notes=f"Manual regime canary launched for {regime_value}",
        )
        approval = get_policy_approval(str(approval.get("approval_id") or "")) or approval
    _insert_policy_rollout_event(
        rollout_id=str((rollout or {}).get("rollout_id") or "") or None,
        approval_id=str(approval.get("approval_id") or "") or None,
        policy_name=policy_name,
        policy_version=policy_version,
        event_type="manual_regime_action",
        event_status="active" if rollout else "approved",
        payload={
            "action": action_value,
            "stage_scope": stage_value,
            "regime_scope": regime_value,
            "actor": actor,
            "replay_run_id": replay.get("run_id"),
        },
    )
    _emit_policy_notification(
        event_type="policy_regime_action",
        level="info",
        message=f"{action_value} applied for {regime_value} via command center.",
        target_name=policy_name,
        approval_id=str(approval.get("approval_id") or "") or None,
        payload={
            "action": action_value,
            "stage_scope": stage_value,
            "regime_scope": regime_value,
            "policy_version": policy_version,
            "rollout_id": (rollout or {}).get("rollout_id"),
        },
    )
    return {
        "action": action_value,
        "stage": stage_value,
        "regime_key": regime_value,
        "profile": profile,
        "replay": replay,
        "approval": approval,
        "rollout": rollout,
        "traffic_percent": int(traffic_percent or _policy_automation_config()["canary_traffic_percent"]),
    }


def get_regime_action_feedback(*, hours: int = 168) -> dict[str, Any]:
    config = _policy_automation_config()
    cutoff = int(time.time()) - max(1, hours) * 3600
    rollout_index = {str(item.get("rollout_id") or ""): item for item in list_policy_rollouts(limit=500, active_only=False)}
    evaluations: list[dict[str, Any]] = []
    by_regime: dict[str, dict[str, Any]] = {}
    for event_type in ("auto_regime_action_started", "manual_regime_action"):
        for event in list_policy_rollout_events(limit=500, event_type=event_type):
            if int(event.get("created_ts") or 0) < cutoff:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            regime_key = str(payload.get("regime_scope") or "")
            action = str(payload.get("action") or "")
            stage_scope = str(payload.get("stage_scope") or "")
            rollout_id = str(event.get("rollout_id") or "")
            rollout = rollout_index.get(rollout_id) if rollout_id else None
            rollout_status = str((rollout or {}).get("rollout_status") or "pending")
            metrics = _canary_metrics_for_rollout(rollout or {"policy_name": event.get("policy_name"), "policy_version": event.get("policy_version"), "stage_scope": stage_scope, "regime_scope": regime_key}, hours=max(1, hours))
            verdict = "pending"
            if rollout_status == "promoted":
                verdict = "correct"
            elif rollout_status == "rolled_back":
                verdict = "incorrect"
            elif metrics["samples"] >= int(config["auto_promote_min_samples"]):
                if metrics["negative_rate"] > float(config["auto_promote_max_negative_rate"]):
                    verdict = "incorrect"
                elif action == "canary_relax" and metrics["positive_rate"] >= float(config["auto_promote_min_positive_rate"]):
                    verdict = "correct"
                elif action == "canary_tighten" and metrics["negative_rate"] <= float(config["auto_promote_max_negative_rate"]):
                    verdict = "correct"
            evaluation = {
                "event_id": event.get("event_id"),
                "event_type": event_type,
                "policy_name": event.get("policy_name"),
                "policy_version": event.get("policy_version"),
                "action": action,
                "stage_scope": stage_scope,
                "regime_key": regime_key,
                "rollout_id": rollout_id or None,
                "rollout_status": rollout_status,
                "verdict": verdict,
                "metrics": metrics,
            }
            evaluations.append(evaluation)
            summary = by_regime.setdefault(
                regime_key,
                {
                    "regime_key": regime_key,
                    "stage_scope": stage_scope,
                    "actions": {},
                    "recommended_action": None,
                },
            )
            action_summary = summary["actions"].setdefault(
                action,
                {"correct": 0, "incorrect": 0, "pending": 0},
            )
            action_summary[verdict] = int(action_summary.get(verdict) or 0) + 1
    for summary in by_regime.values():
        relax_score = int(summary["actions"].get("canary_relax", {}).get("correct", 0)) - int(summary["actions"].get("canary_relax", {}).get("incorrect", 0))
        tighten_score = int(summary["actions"].get("canary_tighten", {}).get("correct", 0)) - int(summary["actions"].get("canary_tighten", {}).get("incorrect", 0))
        summary["recommended_action"] = "canary_relax" if relax_score > tighten_score else "canary_tighten"
    return {"hours": max(1, hours), "evaluations": evaluations, "by_regime": by_regime}


def get_regime_meta_policy(*, hours: int = 168) -> dict[str, Any]:
    config = _policy_automation_config()
    feedback = get_regime_action_feedback(hours=max(1, hours))
    regime_summary = get_policy_regime_summary(hours=max(24, min(hours, 168)), limit=100)
    summaries = regime_summary.get("regimes") if isinstance(regime_summary.get("regimes"), list) else []
    by_regime: dict[str, dict[str, Any]] = {}
    for item in summaries:
        if not isinstance(item, dict):
            continue
        regime_key = str(item.get("regime_key") or "")
        if not regime_key:
            continue
        feedback_item = (feedback.get("by_regime") if isinstance(feedback.get("by_regime"), dict) else {}).get(regime_key, {})
        positive = int(item.get("positive_outcomes") or 0)
        negative = int(item.get("negative_outcomes") or 0)
        decisions = int(item.get("decision_count") or 0)
        action_map = feedback_item.get("actions") if isinstance(feedback_item, dict) and isinstance(feedback_item.get("actions"), dict) else {}
        correct = sum(int((action_map.get(name) or {}).get("correct", 0)) for name in ("canary_relax", "canary_tighten"))
        incorrect = sum(int((action_map.get(name) or {}).get("incorrect", 0)) for name in ("canary_relax", "canary_tighten"))
        evidence = positive + negative + correct + incorrect
        outcome_edge = positive - negative
        feedback_edge = correct - incorrect
        confidence_score = max(0.0, min(100.0, 50.0 + (outcome_edge * 8.0) + (feedback_edge * 12.0) + min(decisions, 10) * 1.5))
        if confidence_score >= 75.0:
            aggressiveness = "aggressive"
            traffic_percent = min(50, max(int(config["canary_traffic_percent"]), int(config["canary_traffic_percent"]) * 2))
            cooldown_multiplier = 0.5
        elif confidence_score >= 55.0:
            aggressiveness = "balanced"
            traffic_percent = int(config["canary_traffic_percent"])
            cooldown_multiplier = 1.0
        else:
            aggressiveness = "conservative"
            traffic_percent = max(5, int(config["canary_traffic_percent"]) // 2)
            cooldown_multiplier = 1.5
        recommended_action = "canary_relax" if outcome_edge >= 0 else "canary_tighten"
        if isinstance(feedback_item, dict) and feedback_item.get("recommended_action"):
            recommended_action = str(feedback_item.get("recommended_action") or recommended_action)
        by_regime[regime_key] = {
            "regime_key": regime_key,
            "stage_scope": str(item.get("stage") or ""),
            "confidence_score": round(confidence_score, 1),
            "aggressiveness": aggressiveness,
            "traffic_percent": int(traffic_percent),
            "cooldown_multiplier": float(cooldown_multiplier),
            "recommended_action": recommended_action,
            "evidence_count": evidence,
            "positive_outcomes": positive,
            "negative_outcomes": negative,
            "correct_actions": correct,
            "incorrect_actions": incorrect,
        }
    return {"hours": max(1, hours), "by_regime": by_regime}


def get_policy_portfolio_budget(*, hours: int = 168) -> dict[str, Any]:
    config = _policy_automation_config()
    meta_policy = get_regime_meta_policy(hours=max(1, hours))
    active_canaries = [
        item for item in list_policy_rollouts(limit=200, active_only=True) if str(item.get("rollout_mode") or "") == "canary"
    ]
    total_traffic_used = sum(int(item.get("traffic_percent") or 0) for item in active_canaries)
    high_risk_active = 0
    for item in active_canaries:
        regime_key = str(item.get("regime_scope") or "")
        meta = (meta_policy.get("by_regime") if isinstance(meta_policy.get("by_regime"), dict) else {}).get(regime_key, {})
        if isinstance(meta, dict) and str(meta.get("aggressiveness") or "") == "conservative":
            high_risk_active += 1
    return {
        "total_canary_traffic_used": total_traffic_used,
        "total_canary_traffic_max": int(config["max_total_canary_traffic_percent"]),
        "total_canary_traffic_remaining": max(0, int(config["max_total_canary_traffic_percent"]) - total_traffic_used),
        "high_risk_active_regimes": high_risk_active,
        "high_risk_active_regimes_max": int(config["max_high_risk_active_regimes"]),
    }


def get_policy_strategy_synthesis(*, hours: int = 168) -> dict[str, Any]:
    meta_policy = get_regime_meta_policy(hours=max(1, hours))
    feedback = get_regime_action_feedback(hours=max(1, hours))
    recent_replays = list_policy_replays(limit=100)
    replay_pressure: dict[str, dict[str, int]] = {}
    for replay in recent_replays:
        regime_key = str(replay.get("regime_key") or "")
        if not regime_key:
            continue
        impact = replay.get("impact") if isinstance(replay.get("impact"), dict) else {}
        bucket = replay_pressure.setdefault(regime_key, {"positive": 0, "negative": 0, "runs": 0})
        bucket["positive"] += int(impact.get("positive_outcomes") or 0)
        bucket["negative"] += int(impact.get("negative_outcomes") or 0)
        bucket["runs"] += 1
    by_regime: dict[str, dict[str, Any]] = {}
    for regime_key, meta in (meta_policy.get("by_regime") if isinstance(meta_policy.get("by_regime"), dict) else {}).items():
        pressure = replay_pressure.get(regime_key, {"positive": 0, "negative": 0, "runs": 0})
        feedback_item = (feedback.get("by_regime") if isinstance(feedback.get("by_regime"), dict) else {}).get(regime_key, {})
        by_regime[regime_key] = {
            "regime_key": regime_key,
            "stage_scope": meta.get("stage_scope"),
            "confidence_score": meta.get("confidence_score"),
            "aggressiveness": meta.get("aggressiveness"),
            "recommended_action": meta.get("recommended_action"),
            "replay_positive": int(pressure["positive"]),
            "replay_negative": int(pressure["negative"]),
            "replay_runs": int(pressure["runs"]),
            "feedback_actions": (feedback_item.get("actions") if isinstance(feedback_item, dict) and isinstance(feedback_item.get("actions"), dict) else {}),
        }
    return {"hours": max(1, hours), "by_regime": by_regime}


def _score_generated_candidate(
    *,
    stage: str,
    regime_key: str | None,
    replay: dict[str, Any],
    novelty_score: float,
    source_signal: str | None,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    impact = replay.get("impact") if isinstance(replay.get("impact"), dict) else {}
    positive = int(impact.get("positive_outcomes") or 0)
    negative = int(impact.get("negative_outcomes") or 0)
    change_rate = float(replay.get("change_rate") or 0.0)
    synthesis = (strategy.get("by_regime") if isinstance(strategy.get("by_regime"), dict) else {}).get(regime_key or "", {})
    confidence = float((synthesis.get("confidence_score") if isinstance(synthesis, dict) else 50.0) or 50.0)
    recommended_action = str((synthesis.get("recommended_action") if isinstance(synthesis, dict) else "") or "")
    action_bias = "tighten" if "positive" not in str(source_signal or "") else "relax"
    alignment_bonus = 0.0
    if recommended_action:
        if recommended_action.endswith(action_bias):
            alignment_bonus = 8.0
        else:
            alignment_bonus = -6.0
    score = (
        35.0
        + (change_rate * 0.35)
        + ((positive - negative) * 10.0)
        + (novelty_score * 25.0)
        + (confidence * 0.25)
        + alignment_bonus
    )
    score = round(max(0.0, min(100.0, score)), 2)
    if score >= 75.0:
        label = "prime"
    elif score >= 55.0:
        label = "qualified"
    else:
        label = "speculative"
    return {"proposal_score": score, "strategy_label": label, "recommended_action": recommended_action or None}


def auto_execute_regime_actions(*, hours: int = 24, replay_limit: int = 250) -> dict[str, Any]:
    config = _policy_automation_config()
    budget_remaining = max(
        0,
        int(config["max_regime_actions_per_day"]) - _recent_policy_event_count({"auto_regime_action_started"}, 86400),
    )
    feedback = get_regime_action_feedback(hours=max(24, hours * 7))
    meta_policy = get_regime_meta_policy(hours=max(24, hours * 7))
    portfolio = get_policy_portfolio_budget(hours=max(24, hours * 7))
    generated = generate_policy_candidates(hours=max(1, hours), generation_limit=int(config["generation_limit"]), replay_limit=max(25, replay_limit))
    candidates = generated.get("generated") if isinstance(generated.get("generated"), list) else []
    candidates = sorted(
        candidates,
        key=lambda item: float(
            (
                (meta_policy.get("by_regime") if isinstance(meta_policy.get("by_regime"), dict) else {})
                .get(str(item.get("regime_key") or ""), {})
                .get("confidence_score", 0.0)
            )
            or 0.0
        ),
        reverse=True,
    )
    executed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_regimes: set[str] = set()
    for item in candidates:
        regime_key = str(item.get("regime_key") or "").strip()
        if not regime_key:
            continue
        if budget_remaining <= 0:
            skipped.append({"regime_key": regime_key, "reason": "regime_action_budget_exhausted"})
            continue
        if regime_key in seen_regimes:
            skipped.append({"regime_key": regime_key, "reason": "duplicate_regime_in_cycle"})
            continue
        replay = item.get("replay") if isinstance(item.get("replay"), dict) else {}
        impact = replay.get("impact") if isinstance(replay.get("impact"), dict) else {}
        positive = int(impact.get("positive_outcomes") or 0)
        negative = int(impact.get("negative_outcomes") or 0)
        stage = str(item.get("stage") or _policy_stage_from_regime_key(regime_key))
        action = "canary_relax" if positive >= negative else "canary_tighten"
        regime_feedback = (feedback.get("by_regime") if isinstance(feedback.get("by_regime"), dict) else {}).get(regime_key)
        if isinstance(regime_feedback, dict) and regime_feedback.get("recommended_action"):
            action = str(regime_feedback.get("recommended_action") or action)
        meta = (meta_policy.get("by_regime") if isinstance(meta_policy.get("by_regime"), dict) else {}).get(regime_key, {})
        requested_traffic = int((meta.get("traffic_percent") if isinstance(meta, dict) else None) or config["canary_traffic_percent"])
        if requested_traffic > int(portfolio["total_canary_traffic_remaining"]):
            skipped.append(
                {
                    "regime_key": regime_key,
                    "stage": stage,
                    "reason": "portfolio_traffic_exhausted",
                    "requested_traffic": requested_traffic,
                    "traffic_remaining": int(portfolio["total_canary_traffic_remaining"]),
                }
            )
            continue
        if (
            isinstance(meta, dict)
            and str(meta.get("aggressiveness") or "") == "conservative"
            and int(portfolio["high_risk_active_regimes"]) >= int(portfolio["high_risk_active_regimes_max"])
        ):
            skipped.append(
                {
                    "regime_key": regime_key,
                    "stage": stage,
                    "reason": "portfolio_high_risk_limit_reached",
                }
            )
            continue
        if isinstance(meta, dict) and meta.get("recommended_action"):
            action = str(meta.get("recommended_action") or action)
        effective_cooldown = int(float(config["regime_action_cooldown_sec"]) * float((meta.get("cooldown_multiplier") if isinstance(meta, dict) else 1.0) or 1.0))
        cooldown_remaining = _cooldown_remaining(
            _latest_policy_event_ts(event_types={"auto_regime_action_started"}, stage_scope=stage, regime_scope=regime_key),
            effective_cooldown,
        )
        if cooldown_remaining > 0:
            skipped.append(
                {
                    "regime_key": regime_key,
                    "stage": stage,
                    "reason": "regime_action_cooldown_active",
                    "aggressiveness": meta.get("aggressiveness") if isinstance(meta, dict) else None,
                    "cooldown_remaining_sec": cooldown_remaining,
                }
            )
            continue
        try:
            result = execute_regime_policy_action(
                regime_key=regime_key,
                action=action,
                stage=stage,
                actor="policy-automation",
                hours=max(1, hours),
                replay_limit=max(25, replay_limit),
                traffic_percent=requested_traffic,
            )
        except ValueError as exc:
            skipped.append({"regime_key": regime_key, "stage": stage, "reason": str(exc)})
            continue
        _insert_policy_rollout_event(
            rollout_id=str((result.get("rollout") or {}).get("rollout_id") or "") or None,
            approval_id=str((result.get("approval") or {}).get("approval_id") or "") or None,
            policy_name=str((result.get("profile") or {}).get("policy_name") or ""),
            policy_version=str((result.get("profile") or {}).get("policy_version") or ""),
            event_type="auto_regime_action_started",
            event_status="active" if result.get("rollout") else "approved",
            payload={
                "action": action,
                "stage_scope": stage,
                "regime_scope": regime_key,
                "aggressiveness": meta.get("aggressiveness") if isinstance(meta, dict) else None,
                "traffic_percent": result.get("traffic_percent"),
                "source_replay_run_id": (replay or {}).get("run_id"),
                "positive_outcomes": positive,
                "negative_outcomes": negative,
            },
        )
        executed.append(result)
        seen_regimes.add(regime_key)
        portfolio["total_canary_traffic_used"] = int(portfolio["total_canary_traffic_used"]) + requested_traffic
        portfolio["total_canary_traffic_remaining"] = max(
            0,
            int(portfolio["total_canary_traffic_max"]) - int(portfolio["total_canary_traffic_used"]),
        )
        if isinstance(meta, dict) and str(meta.get("aggressiveness") or "") == "conservative":
            portfolio["high_risk_active_regimes"] = int(portfolio["high_risk_active_regimes"]) + 1
        budget_remaining -= 1
    return {
        "config": config,
        "feedback": feedback,
        "meta_policy": meta_policy,
        "portfolio": portfolio,
        "generated": generated,
        "executed": executed,
        "skipped": skipped,
    }


def generate_policy_candidates(
    *,
    hours: int = 24,
    generation_limit: int | None = None,
    replay_limit: int | None = None,
) -> dict[str, Any]:
    config = _policy_automation_config()
    candidate_limit = max(1, int(generation_limit or config["generation_limit"]))
    replay_trace_limit = max(25, int(replay_limit or config["generation_replay_limit"]))
    strategy = get_policy_strategy_synthesis(hours=max(24, hours * 7))
    variants = _policy_generation_variants(hours=max(1, hours), generation_limit=candidate_limit)
    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, variant in enumerate(variants, start=1):
        stage = str(variant.get("stage") or "candidate")
        regime_key = str(variant.get("regime_key") or "").strip() or None
        generated_name = f"generated_{stage}_policy"
        generated_version = f"gen-{int(time.time())}-{index}"
        config_payload = dict(variant.get("config") or {})
        config_payload["policy_name"] = generated_name
        config_payload["policy_version"] = generated_version
        if _equivalent_profile_exists(generated_name, generated_version, int(config["duplicate_profile_lookback_hours"])):
            skipped.append({"stage": stage, "reason": "duplicate_profile_fingerprint", "source_signal": variant.get("source_signal")})
            continue
        novelty_score = 0.0
        base_policy = resolve_live_policy(stage, regime_key=regime_key)
        base_config = dict(base_policy.get("config") or {})
        numeric_keys = [
            "candidate_attention_min",
            "candidate_creator_min",
            "promoted_confidence_min",
            "promoted_attention_min",
            "promoted_risk_max",
            "promoted_liquidity_min",
            "promoted_buyers_15m_min",
        ]
        deltas: dict[str, float] = {}
        for key in numeric_keys:
            base_value = _to_float(base_config.get(key))
            generated_value = _to_float(config_payload.get(key))
            if base_value is None or generated_value is None:
                continue
            delta = abs(generated_value - base_value)
            normalized = delta / max(abs(base_value), 1.0)
            deltas[key] = round(delta, 4)
            novelty_score += normalized
        novelty_score = round(novelty_score, 4)
        if novelty_score < float(config["generation_novelty_min"]):
            skipped.append({"stage": stage, "reason": "novelty_too_low", "novelty_score": novelty_score})
            continue
        replay = run_policy_replay(
            hours=max(1, hours),
            limit=replay_trace_limit,
            stage=stage,
            regime_key=regime_key,
            policy_name=generated_name,
            policy_version=generated_version,
            overrides=config_payload,
        )
        proposal = _score_generated_candidate(
            stage=stage,
            regime_key=regime_key,
            replay=replay,
            novelty_score=novelty_score,
            source_signal=str(variant.get("source_signal") or ""),
            strategy=strategy,
        )
        if float(proposal["proposal_score"]) < float(config["generation_proposal_score_min"]):
            skipped.append(
                {
                    "stage": stage,
                    "regime_key": regime_key,
                    "reason": "proposal_score_too_low",
                    "proposal_score": proposal["proposal_score"],
                }
            )
            continue
        profile = create_policy_profile(
            policy_name=generated_name,
            policy_version=generated_version,
            config=config_payload,
            description=(
                "Auto-generated from diagnostics and replay pressure"
                + (f" for {regime_key}" if regime_key else "")
            ),
            created_by="policy-generator",
        )
        payload = {
            "profile": profile,
            "replay": replay,
            "stage": stage,
            "regime_key": regime_key,
            "rationale": list(variant.get("rationale") or []),
            "source_signal": variant.get("source_signal"),
            "novelty_score": novelty_score,
            "deltas": deltas,
            "proposal_score": proposal["proposal_score"],
            "strategy_label": proposal["strategy_label"],
            "recommended_action": proposal["recommended_action"],
        }
        generated.append(payload)
        _insert_policy_rollout_event(
            rollout_id=None,
            approval_id=None,
            policy_name=generated_name,
            policy_version=generated_version,
            event_type="policy_candidate_generated",
            event_status="generated",
            payload={
                "stage": stage,
                "regime_key": regime_key,
                "novelty_score": novelty_score,
                "proposal_score": proposal["proposal_score"],
                "strategy_label": proposal["strategy_label"],
                "source_signal": variant.get("source_signal"),
                "rationale": list(variant.get("rationale") or []),
                "replay_run_id": replay.get("run_id"),
            },
        )
    generated.sort(key=lambda item: float(item.get("proposal_score") or 0.0), reverse=True)
    for index, item in enumerate(generated, start=1):
        item["proposal_rank"] = index
    return {
        "config": config,
        "hours": max(1, hours),
        "strategy": strategy,
        "generated": generated,
        "skipped": skipped,
    }


def _insert_policy_rollout_event(
    *,
    rollout_id: str | None,
    approval_id: str | None,
    policy_name: str,
    policy_version: str,
    event_type: str,
    event_status: str,
    payload: dict[str, Any] | None = None,
) -> None:
    _ensure_schema()
    with _connect() as c:
        c.execute(
            """
            INSERT INTO policy_rollout_events (
                event_id, created_ts, rollout_id, approval_id, policy_name, policy_version,
                event_type, event_status, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                int(time.time()),
                rollout_id,
                approval_id,
                policy_name,
                policy_version,
                event_type,
                event_status,
                _json_dumps(payload or {}),
            ),
        )


def _emit_policy_notification(
    *,
    event_type: str,
    level: str,
    message: str,
    target_name: str,
    approval_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        from app.services.tuning_service import emit_rollout_notification

        emit_rollout_notification(
            event_type=event_type,
            level=level,
            message=message,
            target_name=target_name,
            approval_id=approval_id,
            payload=payload,
        )
    except Exception:
        logger.exception("[policy-automation] notification_failed event_type=%s target=%s", event_type, target_name)


def _replay_negative_rate(replay: dict[str, Any]) -> float:
    impact = replay.get("impact") if isinstance(replay.get("impact"), dict) else {}
    positive = int(impact.get("positive_outcomes") or 0)
    negative = int(impact.get("negative_outcomes") or 0)
    total = positive + negative
    return round((negative / total) * 100.0, 1) if total else 0.0


def _auto_approval_exists(source_ref: str) -> bool:
    for item in list_policy_approvals(limit=200):
        if str(item.get("source_ref") or "") == source_ref:
            return True
    return False


def auto_create_policy_approvals(limit: int | None = None) -> dict[str, Any]:
    config = _policy_automation_config()
    replay_limit = max(1, int(limit or config["replay_limit"]))
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    approval_budget_remaining = max(0, int(config["max_auto_approvals_per_day"]) - _recent_policy_event_count({"auto_approval_created"}, 86400))
    for replay in list_policy_replays(limit=replay_limit):
        shadow_policy = replay.get("shadow_policy") if isinstance(replay.get("shadow_policy"), dict) else {}
        policy_name = str(shadow_policy.get("policy_name") or "")
        policy_version = str(shadow_policy.get("policy_version") or "")
        run_id = str(replay.get("run_id") or "")
        if not policy_name or not policy_version or not run_id:
            continue
        if approval_budget_remaining <= 0:
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "approval_budget_exhausted"})
            continue
        if _auto_approval_exists(run_id):
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "approval_exists"})
            continue
        if _find_policy_profile(policy_name, policy_version) is None:
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "profile_missing"})
            continue
        if _equivalent_profile_exists(policy_name, policy_version, int(config["duplicate_profile_lookback_hours"])):
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "duplicate_profile_fingerprint"})
            continue
        cooldown_remaining = _cooldown_remaining(
            _latest_policy_event_ts(
                event_types={"auto_approval_created"},
                policy_name=policy_name,
                policy_version=policy_version,
            ),
            int(config["auto_approval_cooldown_sec"]),
        )
        if cooldown_remaining > 0:
            skipped.append(
                {
                    "run_id": run_id,
                    "policy_name": policy_name,
                    "reason": "approval_cooldown_active",
                    "cooldown_remaining_sec": cooldown_remaining,
                }
            )
            continue
        trace_count = int(replay.get("trace_count") or 0)
        changed_count = int(replay.get("changed_count") or 0)
        change_rate = float(replay.get("change_rate") or 0.0)
        negative_rate = _replay_negative_rate(replay)
        impact = replay.get("impact") if isinstance(replay.get("impact"), dict) else {}
        positive_outcomes = int(impact.get("positive_outcomes") or 0)
        if trace_count < int(config["auto_approval_min_trace_count"]):
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "trace_count_too_low"})
            continue
        if changed_count < int(config["auto_approval_min_changed_count"]):
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "changed_count_too_low"})
            continue
        if change_rate < float(config["auto_approval_min_change_rate"]):
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "change_rate_too_low"})
            continue
        if negative_rate > float(config["auto_approval_max_negative_rate"]):
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "negative_rate_too_high"})
            continue
        if positive_outcomes < int(config["auto_approval_min_positive_outcomes"]):
            skipped.append({"run_id": run_id, "policy_name": policy_name, "reason": "positive_outcomes_too_low"})
            continue
        approval = create_policy_approval(
            policy_name=policy_name,
            policy_version=policy_version,
            source_type="replay",
            source_ref=run_id,
            notes=(
                f"Auto-approved from replay {run_id}: traces={trace_count}, changed={changed_count}, "
                f"change_rate={change_rate}%, replay_negative_rate={negative_rate}%"
            ),
            approved_by="policy-automation",
        )
        _insert_policy_rollout_event(
            rollout_id=None,
            approval_id=str(approval.get("approval_id") or ""),
            policy_name=policy_name,
            policy_version=policy_version,
            event_type="auto_approval_created",
            event_status=str(approval.get("approval_status") or "approved"),
            payload={
                "run_id": run_id,
                "trace_count": trace_count,
                "changed_count": changed_count,
                "change_rate": change_rate,
                "replay_negative_rate": negative_rate,
            },
        )
        _emit_policy_notification(
            event_type="policy_auto_approval_created",
            level="info",
            message=f"Auto-approved {policy_name}@{policy_version} from replay {run_id}.",
            target_name=policy_name,
            approval_id=str(approval.get("approval_id") or ""),
            payload={"run_id": run_id, "policy_version": policy_version},
        )
        created.append(approval)
        approval_budget_remaining -= 1
    return {
        "config": config,
        "created": created,
        "skipped": skipped,
    }


def _canary_metrics_for_rollout(rollout: dict[str, Any], hours: int) -> dict[str, Any]:
    cutoff = int(time.time()) - max(1, hours) * 3600
    with _connect() as c:
        rows = c.execute(
            """
            SELECT ss.outcome_label, sd.features_json
            FROM signal_decisions sd
            LEFT JOIN signal_snapshots ss
              ON ss.signal_id = sd.signal_id
             AND ss.horizon_minutes = (
                SELECT MAX(horizon_minutes)
                FROM signal_snapshots ss2
                WHERE ss2.signal_id = sd.signal_id
             )
            WHERE sd.policy_name=?
              AND sd.policy_version=?
              AND sd.created_ts >= ?
              AND (? IS NULL OR sd.stage = ?)
            """,
            (
                rollout["policy_name"],
                rollout["policy_version"],
                cutoff,
                rollout.get("stage_scope"),
                rollout.get("stage_scope"),
            ),
        ).fetchall()
    total = 0
    positive = 0
    negative = 0
    pending = 0
    for row in rows:
        features: dict[str, Any] = {}
        try:
            features = json.loads((row[1] if len(row) > 1 else None) or "{}")
            if not isinstance(features, dict):
                features = {}
        except Exception:
            features = {}
        if str(rollout.get("regime_scope") or "") and str(features.get("regime_key") or "") != str(rollout.get("regime_scope") or ""):
            continue
        outcome_label = str((row[0] if row else None) or "pending")
        if outcome_label == "pending":
            pending += 1
            continue
        total += 1
        if outcome_label in {"worked", "strong_continuation"}:
            positive += 1
        elif outcome_label in {"failed", "faded"}:
            negative += 1
    return {
        "samples": total,
        "positive": positive,
        "negative": negative,
        "pending": pending,
        "positive_rate": round((positive / total) * 100.0, 1) if total else 0.0,
        "negative_rate": round((negative / total) * 100.0, 1) if total else 0.0,
    }


def auto_schedule_policy_canaries(hours: int = 24) -> dict[str, Any]:
    config = _policy_automation_config()
    meta_policy = get_regime_meta_policy(hours=max(24, hours * 7))
    portfolio = get_policy_portfolio_budget(hours=max(24, hours * 7))
    rollouts = list_policy_rollouts(limit=200, active_only=False)
    approvals = list_policy_approvals(limit=200, approval_status="approved")
    scheduled: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    canary_budget_remaining = max(0, int(config["max_canary_rollouts_per_day"]) - _recent_policy_event_count({"auto_canary_started"}, 86400))
    for approval in approvals:
        policy_name = str(approval.get("policy_name") or "")
        policy_version = str(approval.get("policy_version") or "")
        approval_id = str(approval.get("approval_id") or "")
        if canary_budget_remaining <= 0:
            skipped.append({"approval_id": approval_id, "policy_name": policy_name, "reason": "canary_budget_exhausted"})
            continue
        if any(
            str(item.get("policy_name") or "") == policy_name
            and str(item.get("policy_version") or "") == policy_version
            and str(item.get("rollout_status") or "") in {"active", "promoted"}
            for item in rollouts
        ):
            skipped.append({"approval_id": approval_id, "policy_name": policy_name, "reason": "rollout_exists"})
            continue
        stage_scope = None
        regime_scope = None
        source_ref = str(approval.get("source_ref") or "")
        if str(approval.get("source_type") or "") == "replay" and source_ref:
            replay = get_policy_replay(source_ref)
            if replay is not None:
                stage_scope = str(replay.get("stage") or "") or None
                regime_scope = str(replay.get("regime_key") or "") or None
        meta = (meta_policy.get("by_regime") if isinstance(meta_policy.get("by_regime"), dict) else {}).get(regime_scope or "", {})
        requested_traffic = int((meta.get("traffic_percent") if isinstance(meta, dict) else None) or config["canary_traffic_percent"])
        active_stage_canaries = _active_canary_count(stage_scope, regime_scope)
        if active_stage_canaries >= int(config["max_active_canaries_per_stage"]):
            skipped.append(
                {
                    "approval_id": approval_id,
                    "policy_name": policy_name,
                    "reason": "stage_canary_limit_reached",
                    "stage_scope": stage_scope,
                    "regime_scope": regime_scope,
                    "active_stage_canaries": active_stage_canaries,
                }
            )
            continue
        if requested_traffic > int(portfolio["total_canary_traffic_remaining"]):
            skipped.append(
                {
                    "approval_id": approval_id,
                    "policy_name": policy_name,
                    "reason": "portfolio_traffic_exhausted",
                    "stage_scope": stage_scope,
                    "regime_scope": regime_scope,
                    "requested_traffic": requested_traffic,
                    "traffic_remaining": int(portfolio["total_canary_traffic_remaining"]),
                }
            )
            continue
        if (
            isinstance(meta, dict)
            and str(meta.get("aggressiveness") or "") == "conservative"
            and int(portfolio["high_risk_active_regimes"]) >= int(portfolio["high_risk_active_regimes_max"])
        ):
            skipped.append(
                {
                    "approval_id": approval_id,
                    "policy_name": policy_name,
                    "reason": "portfolio_high_risk_limit_reached",
                    "stage_scope": stage_scope,
                    "regime_scope": regime_scope,
                }
            )
            continue
        cooldown_remaining = _cooldown_remaining(
            _latest_policy_event_ts(event_types={"auto_canary_started"}, stage_scope=stage_scope, regime_scope=regime_scope),
            int(config["canary_cooldown_sec"]),
        )
        if cooldown_remaining > 0:
            skipped.append(
                {
                    "approval_id": approval_id,
                    "policy_name": policy_name,
                    "reason": "canary_cooldown_active",
                    "stage_scope": stage_scope,
                    "regime_scope": regime_scope,
                    "cooldown_remaining_sec": cooldown_remaining,
                }
            )
            continue
        baseline = resolve_live_policy(stage_scope or "promoted", regime_key=regime_scope)
        rollout = activate_policy_rollout(
            policy_name=policy_name,
            policy_version=policy_version,
            rollout_mode="canary",
            rollout_status="active",
            stage_scope=stage_scope,
            regime_scope=regime_scope,
            traffic_percent=requested_traffic,
            priority=int(config["canary_priority"]),
            activated_by="policy-automation",
            notes=f"Auto-canary from approval {approval_id}",
        )
        update_policy_approval_status(
            approval_id,
            approval_status="rolled_out",
            approved_by="policy-automation",
            notes=f"Auto-canary started with {requested_traffic}% traffic",
        )
        _insert_policy_rollout_event(
            rollout_id=str(rollout.get("rollout_id") or ""),
            approval_id=approval_id,
            policy_name=policy_name,
            policy_version=policy_version,
            event_type="auto_canary_started",
            event_status="active",
            payload={
                "stage_scope": stage_scope,
                "regime_scope": regime_scope,
                "traffic_percent": requested_traffic,
                "baseline_policy_name": baseline.get("policy_name"),
                "baseline_policy_version": baseline.get("policy_version"),
            },
        )
        _emit_policy_notification(
            event_type="policy_canary_started",
            level="info",
            message=f"Started canary for {policy_name}@{policy_version} on {stage_scope or 'all'} at {requested_traffic}% traffic.",
            target_name=policy_name,
            approval_id=approval_id,
            payload={
                "policy_version": policy_version,
                "stage_scope": stage_scope,
                "regime_scope": regime_scope,
                "traffic_percent": requested_traffic,
                "baseline_policy_name": baseline.get("policy_name"),
                "baseline_policy_version": baseline.get("policy_version"),
            },
        )
        scheduled.append(rollout)
        rollouts.append(rollout)
        portfolio["total_canary_traffic_used"] = int(portfolio["total_canary_traffic_used"]) + requested_traffic
        portfolio["total_canary_traffic_remaining"] = max(
            0,
            int(portfolio["total_canary_traffic_max"]) - int(portfolio["total_canary_traffic_used"]),
        )
        if isinstance(meta, dict) and str(meta.get("aggressiveness") or "") == "conservative":
            portfolio["high_risk_active_regimes"] = int(portfolio["high_risk_active_regimes"]) + 1
        canary_budget_remaining -= 1
    return {"config": config, "portfolio": portfolio, "scheduled": scheduled, "skipped": skipped}


def auto_promote_policy_canaries(hours: int = 24) -> dict[str, Any]:
    config = _policy_automation_config()
    promoted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    active_rollouts = list_policy_rollouts(limit=200, active_only=True)
    approvals = list_policy_approvals(limit=200)
    promotion_budget_remaining = max(0, int(config["max_promotions_per_day"]) - _recent_policy_event_count({"canary_promoted"}, 86400))
    for rollout in active_rollouts:
        if str(rollout.get("rollout_mode") or "") != "canary":
            continue
        if promotion_budget_remaining <= 0:
            skipped.append({"rollout_id": rollout["rollout_id"], "reason": "promotion_budget_exhausted"})
            continue
        cooldown_remaining = _cooldown_remaining(
            _latest_policy_event_ts(
                event_types={"canary_promoted"},
                stage_scope=str(rollout.get("stage_scope") or "") or None,
                regime_scope=str(rollout.get("regime_scope") or "") or None,
            ),
            int(config["promotion_cooldown_sec"]),
        )
        if cooldown_remaining > 0:
            skipped.append(
                {
                    "rollout_id": rollout["rollout_id"],
                    "reason": "promotion_cooldown_active",
                    "cooldown_remaining_sec": cooldown_remaining,
                    "stage_scope": rollout.get("stage_scope"),
                    "regime_scope": rollout.get("regime_scope"),
                }
            )
            continue
        metrics = _canary_metrics_for_rollout(rollout, hours=max(1, hours))
        if metrics["samples"] < int(config["auto_promote_min_samples"]):
            skipped.append({"rollout_id": rollout["rollout_id"], "reason": "samples_too_low", **metrics})
            continue
        if metrics["negative_rate"] > float(config["auto_promote_max_negative_rate"]):
            skipped.append({"rollout_id": rollout["rollout_id"], "reason": "negative_rate_too_high", **metrics})
            continue
        if metrics["positive_rate"] < float(config["auto_promote_min_positive_rate"]):
            skipped.append({"rollout_id": rollout["rollout_id"], "reason": "positive_rate_too_low", **metrics})
            continue
        baseline = resolve_live_policy(
            str(rollout.get("stage_scope") or "promoted"),
            regime_key=str(rollout.get("regime_scope") or "") or None,
        )
        with _connect() as c:
            c.execute(
                "UPDATE policy_rollouts SET rollout_status='promoted' WHERE rollout_id=?",
                (rollout["rollout_id"],),
            )
        active = activate_policy_rollout(
            policy_name=str(rollout.get("policy_name") or ""),
            policy_version=str(rollout.get("policy_version") or ""),
            rollout_mode="active",
            rollout_status="active",
            stage_scope=str(rollout.get("stage_scope") or "") or None,
            regime_scope=str(rollout.get("regime_scope") or "") or None,
            traffic_percent=100,
            priority=max(1, int(rollout.get("priority") or 1)),
            activated_by="policy-automation",
            notes=f"Auto-promoted from canary {rollout['rollout_id']}",
        )
        approval_id = None
        for approval in approvals:
            if (
                str(approval.get("policy_name") or "") == str(rollout.get("policy_name") or "")
                and str(approval.get("policy_version") or "") == str(rollout.get("policy_version") or "")
            ):
                approval_id = str(approval.get("approval_id") or "") or None
                break
        _insert_policy_rollout_event(
            rollout_id=str(active.get("rollout_id") or ""),
            approval_id=approval_id,
            policy_name=str(rollout.get("policy_name") or ""),
            policy_version=str(rollout.get("policy_version") or ""),
            event_type="canary_promoted",
            event_status="active",
            payload={
                "from_rollout_id": rollout["rollout_id"],
                "stage_scope": rollout.get("stage_scope"),
                "regime_scope": rollout.get("regime_scope"),
                "baseline_policy_name": baseline.get("policy_name"),
                "baseline_policy_version": baseline.get("policy_version"),
                "metrics": metrics,
            },
        )
        _emit_policy_notification(
            event_type="policy_canary_promoted",
            level="info",
            message=(
                f"Promoted canary {rollout.get('policy_name')}@{rollout.get('policy_version')} "
                f"to active after {metrics['samples']} scored outcomes."
            ),
            target_name=str(rollout.get("policy_name") or ""),
            approval_id=approval_id,
            payload={
                "policy_version": rollout.get("policy_version"),
                "stage_scope": rollout.get("stage_scope"),
                "regime_scope": rollout.get("regime_scope"),
                "metrics": metrics,
                "baseline_policy_name": baseline.get("policy_name"),
                "baseline_policy_version": baseline.get("policy_version"),
            },
        )
        promoted.append({"canary_rollout_id": rollout["rollout_id"], "active_rollout": active, "metrics": metrics})
        promotion_budget_remaining -= 1
    return {"config": config, "promoted": promoted, "skipped": skipped}


def run_policy_automation_cycle(hours: int = 24, replay_limit: int | None = None) -> dict[str, Any]:
    requested_hours = max(1, hours)
    requested_limit = max(1, int(replay_limit or _policy_automation_config()["replay_limit"]))
    automation_run_id = uuid.uuid4().hex
    created_ts = int(time.time())
    with _connect() as c:
        c.execute(
            """
            INSERT INTO policy_automation_runs (
                run_id, created_ts, completed_ts, hours, replay_limit, status, summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                automation_run_id,
                created_ts,
                None,
                requested_hours,
                requested_limit,
                "running",
                _json_dumps({"run_id": automation_run_id, "status": "running"}),
            ),
        )
    try:
        regime_actions = auto_execute_regime_actions(hours=requested_hours, replay_limit=requested_limit)
        approvals = auto_create_policy_approvals(limit=requested_limit)
        canaries = auto_schedule_policy_canaries(hours=requested_hours)
        guardrails = evaluate_policy_guardrails(hours=requested_hours, min_samples=3, max_negative_rate=60.0, auto_apply=True)
        promotions = auto_promote_policy_canaries(hours=requested_hours)
        summary = {
            "run_id": automation_run_id,
            "created_ts": created_ts,
            "completed_ts": int(time.time()),
            "hours": requested_hours,
            "replay_limit": requested_limit,
            "status": "completed",
            "config": _policy_automation_config(),
            "regime_actions": regime_actions,
            "generated": regime_actions.get("generated") if isinstance(regime_actions, dict) else {},
            "approvals": approvals,
            "canaries": canaries,
            "guardrails": guardrails,
            "promotions": promotions,
        }
    except Exception as exc:
        summary = {
            "run_id": automation_run_id,
            "created_ts": created_ts,
            "completed_ts": int(time.time()),
            "hours": requested_hours,
            "replay_limit": requested_limit,
            "status": "failed",
            "error": str(exc),
        }
        with _connect() as c:
            c.execute(
                """
                UPDATE policy_automation_runs
                SET completed_ts=?, status=?, summary_json=?
                WHERE run_id=?
                """,
                (
                    int(summary["completed_ts"]),
                    "failed",
                    _json_dumps(summary),
                    automation_run_id,
                ),
            )
        raise
    with _connect() as c:
        c.execute(
            """
            UPDATE policy_automation_runs
            SET completed_ts=?, status=?, summary_json=?
            WHERE run_id=?
            """,
            (
                int(summary["completed_ts"]),
                "completed",
                _json_dumps(summary),
                automation_run_id,
            ),
        )
    skip_counts = {
        "regime_actions": len((summary.get("regime_actions") or {}).get("skipped") or []),
        "generated": len((summary.get("generated") or {}).get("skipped") or []),
        "approvals": len((summary.get("approvals") or {}).get("skipped") or []),
        "canaries": len((summary.get("canaries") or {}).get("skipped") or []),
        "promotions": len((summary.get("promotions") or {}).get("skipped") or []),
    }
    _emit_policy_notification(
        event_type="policy_automation_summary",
        level="info",
        message=(
            f"Policy automation run {automation_run_id} completed: "
            f"{len((summary.get('regime_actions') or {}).get('executed') or [])} regime actions, "
            f"{len((summary.get('generated') or {}).get('generated') or [])} generated, "
            f"{len((summary.get('approvals') or {}).get('created') or [])} approvals, "
            f"{len((summary.get('canaries') or {}).get('scheduled') or [])} canaries, "
            f"{len((summary.get('promotions') or {}).get('promoted') or [])} promotions."
        ),
        target_name="policy-automation",
        payload={
            "run_id": automation_run_id,
            "hours": requested_hours,
            "replay_limit": requested_limit,
            "skip_counts": skip_counts,
            "guardrail_rollbacks": len(
                [item for item in ((summary.get("guardrails") or {}).get("evaluations") or []) if item.get("applied")]
            ),
        },
    )
    return summary


def list_policy_automation_runs(limit: int = 20) -> list[dict[str, Any]]:
    _ensure_schema()
    with _connect() as c:
        rows = c.execute(
            """
            SELECT summary_json
            FROM policy_automation_runs
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
    runs: list[dict[str, Any]] = []
    for row in rows:
        if not row or not row[0]:
            continue
        try:
            payload = json.loads(row[0] or "{}")
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            runs.append(payload)
    return runs


def get_latest_policy_automation_run() -> dict[str, Any] | None:
    runs = list_policy_automation_runs(limit=1)
    return runs[0] if runs else None


def _policy_automation_poll_seconds() -> int:
    return max(60, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTOMATION_POLL_SECONDS", "300")))


async def policy_automation_worker() -> None:
    init()
    while True:
        try:
            result = run_policy_automation_cycle(
                hours=max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTOMATION_HOURS", "24"))),
                replay_limit=max(1, int(os.getenv("SIGNAL_ENGINE_POLICY_AUTOMATION_REPLAY_LIMIT", "20"))),
            )
            logger.info(
                "[policy-automation] run=%s approvals=%s canaries=%s promotions=%s guardrail_rollbacks=%s",
                str(result.get("run_id") or ""),
                len((result.get("approvals") or {}).get("created") or []),
                len((result.get("canaries") or {}).get("scheduled") or []),
                len((result.get("promotions") or {}).get("promoted") or []),
                len(
                    [
                        item
                        for item in ((result.get("guardrails") or {}).get("evaluations") or [])
                        if item.get("applied")
                    ]
                ),
            )
        except Exception as exc:
            logger.exception("[policy-automation] worker iteration failed: %s", exc)
        await asyncio.sleep(_policy_automation_poll_seconds())


def get_diagnostics_summary(hours: int = 24) -> dict[str, Any]:
    _ensure_schema()
    cutoff = int(time.time()) - max(1, hours) * 3600
    with _connect() as c:
        decision_rows = c.execute(
            """
            SELECT signal_id, decision, reasons_json, session_bucket, local_daypart,
                   attention_score, risk_score, confidence_score, created_ts, token, stage
            FROM signal_decisions
            WHERE created_ts >= ?
            ORDER BY created_ts DESC
            """,
            (cutoff,),
        ).fetchall()
        outcome_rows = c.execute(
            """
            SELECT
                s.signal_id,
                s.token,
                s.event_type,
                s.session_bucket,
                s.local_daypart,
                s.alert_ts,
                ss.horizon_minutes,
                ss.outcome_label,
                ss.market_cap_change_pct,
                ss.liquidity_change_pct
            FROM signals s
            LEFT JOIN signal_snapshots ss
              ON ss.signal_id = s.signal_id
             AND ss.horizon_minutes = (
                SELECT MAX(horizon_minutes)
                FROM signal_snapshots ss2
                WHERE ss2.signal_id = s.signal_id
             )
            WHERE s.alert_ts >= ?
            ORDER BY s.alert_ts DESC
            """,
            (cutoff,),
        ).fetchall()

    counts_by_decision: dict[str, int] = {}
    skip_reason_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    sessions: dict[str, dict[str, int]] = {}
    recent_examples: list[dict[str, Any]] = []
    outcome_by_token: dict[str, list[dict[str, Any]]] = {}
    outcome_by_signal_id: dict[str, dict[str, Any]] = {}
    outcome_counts: dict[str, int] = {}
    false_positives: list[dict[str, Any]] = []
    session_outcomes: dict[str, dict[str, Any]] = {}
    session_signal_outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    session_signal_daily: dict[tuple[str, str, str], dict[str, Any]] = {}
    conversion_by_token: dict[str, set[str]] = {}
    reason_scorecards: dict[str, dict[str, Any]] = {}
    reason_daily: dict[tuple[str, str], dict[str, Any]] = {}

    for row in outcome_rows:
        (
            signal_id,
            token,
            event_type,
            session_bucket,
            local_daypart,
            alert_ts,
            horizon_minutes,
            outcome_label,
            market_cap_change_pct,
            liquidity_change_pct,
        ) = row
        token_key = token or "unknown"
        session_key = session_bucket or "unknown"
        outcome_label = outcome_label or "pending"
        outcome_counts[outcome_label] = outcome_counts.get(outcome_label, 0) + 1
        conversion_by_token.setdefault(token_key, set()).add(str(event_type or "unknown"))
        outcome_entry = {
            "signal_id": signal_id,
            "token": token,
            "event_type": event_type,
            "session_bucket": session_key,
            "local_daypart": local_daypart,
            "alert_ts": alert_ts,
            "alert_date": datetime.fromtimestamp(int(alert_ts or 0), tz=timezone.utc).date().isoformat() if alert_ts else None,
            "horizon_minutes": horizon_minutes,
            "outcome_label": outcome_label,
            "market_cap_change_pct": market_cap_change_pct,
            "liquidity_change_pct": liquidity_change_pct,
        }
        outcome_by_token.setdefault(token_key, []).append(outcome_entry)
        outcome_by_signal_id[signal_id] = outcome_entry
        session_stats = session_outcomes.setdefault(
            session_key,
            {"total": 0, "worked": 0, "failed": 0, "strong_continuation": 0, "mixed": 0, "pending": 0},
        )
        session_stats["total"] += 1
        if outcome_label in session_stats:
            session_stats[outcome_label] += 1
        elif outcome_label in {"worked", "strong_continuation"}:
            session_stats["worked"] += 1
        elif outcome_label in {"failed", "faded"}:
            session_stats["failed"] += 1
        else:
            session_stats["mixed"] += 1

        combo_key = (session_key, str(event_type or "unknown"))
        combo_stats = session_signal_outcomes.setdefault(
            combo_key,
            {
                "session_bucket": session_key,
                "signal_type": str(event_type or "unknown"),
                "total": 0,
                "worked": 0,
                "strong_continuation": 0,
                "failed": 0,
                "faded": 0,
                "mixed": 0,
                "pending": 0,
            },
        )
        combo_stats["total"] += 1
        if outcome_label in combo_stats:
            combo_stats[outcome_label] += 1
        else:
            combo_stats["mixed"] += 1
        alert_date = outcome_entry["alert_date"] or "unknown"
        combo_daily = session_signal_daily.setdefault(
            (session_key, str(event_type or "unknown"), alert_date),
            {
                "session_bucket": session_key,
                "signal_type": str(event_type or "unknown"),
                "date": alert_date,
                "total": 0,
                "positive": 0,
                "negative": 0,
            },
        )
        combo_daily["total"] += 1
        if outcome_label in {"worked", "strong_continuation"}:
            combo_daily["positive"] += 1
        elif outcome_label in {"failed", "faded"}:
            combo_daily["negative"] += 1

        if event_type in {"candidate", "promoted"} and outcome_label in {"failed", "faded"}:
            false_positives.append(
                {
                    "token": token,
                    "event_type": event_type,
                    "outcome_label": outcome_label,
                    "session_bucket": session_key,
                    "market_cap_change_pct": market_cap_change_pct,
                    "liquidity_change_pct": liquidity_change_pct,
                    "horizon_minutes": horizon_minutes,
                }
            )

    for row in decision_rows:
        signal_id, decision, reasons_json, session_bucket, local_daypart, attention_score, risk_score, confidence_score, created_ts, token, stage = row
        counts_by_decision[decision] = counts_by_decision.get(decision, 0) + 1
        stage_key = stage or "unknown"
        stage_counts[stage_key] = stage_counts.get(stage_key, 0) + 1
        session_stats = sessions.setdefault(session_bucket or "unknown", {"sent": 0, "skipped": 0, "blocked": 0})
        if decision.endswith("sent"):
            session_stats["sent"] += 1
        elif "skip" in decision:
            session_stats["skipped"] += 1
        elif "block" in decision:
            session_stats["blocked"] += 1

        reasons: list[str] = []
        try:
            parsed = json.loads(reasons_json or "[]")
            if isinstance(parsed, list):
                reasons = [str(item) for item in parsed]
        except Exception:
            reasons = []
        if decision != "candidate_sent":
            for reason in reasons:
                skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
        if len(recent_examples) < 20:
            recent_examples.append(
                {
                    "token": token,
                    "signal_id": signal_id,
                    "stage": stage,
                    "decision": decision,
                    "reasons": reasons,
                    "attention_score": attention_score,
                    "risk_score": risk_score,
                    "confidence_score": confidence_score,
                    "session_bucket": session_bucket,
                    "local_daypart": local_daypart,
                    "created_ts": created_ts,
                }
            )

    top_skip_reasons = [
        {"reason": reason, "count": count}
        for reason, count in sorted(skip_reason_counts.items(), key=lambda item: (-item[1], item[0]))[:15]
    ]

    false_negatives: list[dict[str, Any]] = []
    for row in decision_rows:
        signal_id, decision, reasons_json, session_bucket, local_daypart, attention_score, risk_score, confidence_score, created_ts, token, stage = row
        reasons: list[str] = []
        try:
            parsed = json.loads(reasons_json or "[]")
            if isinstance(parsed, list):
                reasons = [str(item) for item in parsed]
        except Exception:
            reasons = []
        matched_outcome = outcome_by_signal_id.get(signal_id or "")
        if matched_outcome is None:
            token_outcomes = outcome_by_token.get(token or "")
            if token_outcomes:
                for outcome_entry in token_outcomes:
                    if outcome_entry["outcome_label"] != "pending":
                        matched_outcome = outcome_entry
                        break
        if matched_outcome:
            outcome_label = str(matched_outcome.get("outcome_label") or "pending")
            outcome_date = str(matched_outcome.get("alert_date") or "unknown")
            for reason in reasons:
                card = reason_scorecards.setdefault(
                    reason,
                    {
                        "reason": reason,
                        "total": 0,
                        "positive": 0,
                        "negative": 0,
                        "worked": 0,
                        "strong_continuation": 0,
                        "failed": 0,
                        "faded": 0,
                        "mixed": 0,
                        "pending": 0,
                    },
                )
                card["total"] += 1
                if outcome_label in {"worked", "strong_continuation"}:
                    card["positive"] += 1
                if outcome_label in {"failed", "faded"}:
                    card["negative"] += 1
                if outcome_label in card:
                    card[outcome_label] += 1
                else:
                    card["mixed"] += 1
                daily_card = reason_daily.setdefault(
                    (reason, outcome_date),
                    {"reason": reason, "date": outcome_date, "total": 0, "positive": 0, "negative": 0},
                )
                daily_card["total"] += 1
                if outcome_label in {"worked", "strong_continuation"}:
                    daily_card["positive"] += 1
                elif outcome_label in {"failed", "faded"}:
                    daily_card["negative"] += 1
        if decision.endswith("sent"):
            continue
        if not matched_outcome or matched_outcome["outcome_label"] not in {"worked", "strong_continuation"}:
            continue
        false_negatives.append(
            {
                "token": token,
                "signal_id": signal_id,
                "stage": stage,
                "decision": decision,
                "reasons": reasons,
                "outcome_label": matched_outcome["outcome_label"],
                "session_bucket": matched_outcome["session_bucket"],
                "market_cap_change_pct": matched_outcome["market_cap_change_pct"],
                "horizon_minutes": matched_outcome["horizon_minutes"],
            }
        )

    conversion = {
        "candidate_tokens": 0,
        "promoted_tokens": 0,
        "candidate_to_promoted_tokens": 0,
    }
    for event_types in conversion_by_token.values():
        if "candidate" in event_types:
            conversion["candidate_tokens"] += 1
        if "promoted" in event_types:
            conversion["promoted_tokens"] += 1
        if "candidate" in event_types and "promoted" in event_types:
            conversion["candidate_to_promoted_tokens"] += 1

    session_quality: list[dict[str, Any]] = []
    for session_name, stats in session_outcomes.items():
        total = int(stats.get("total") or 0)
        positive = int(stats.get("worked") or 0) + int(stats.get("strong_continuation") or 0)
        negative = int(stats.get("failed") or 0)
        win_rate = round((positive / total) * 100.0, 1) if total else 0.0
        fail_rate = round((negative / total) * 100.0, 1) if total else 0.0
        session_quality.append(
            {
                "session_bucket": session_name,
                "total": total,
                "positive": positive,
                "negative": negative,
                "win_rate": win_rate,
                "fail_rate": fail_rate,
            }
        )
    session_quality.sort(key=lambda item: (-item["win_rate"], -item["total"], item["session_bucket"]))

    session_signal_quality: list[dict[str, Any]] = []
    for (session_name, signal_type), stats in session_signal_outcomes.items():
        total = int(stats.get("total") or 0)
        positive = int(stats.get("worked") or 0) + int(stats.get("strong_continuation") or 0)
        negative = int(stats.get("failed") or 0) + int(stats.get("faded") or 0)
        session_signal_quality.append(
            {
                **stats,
                "session_bucket": session_name,
                "signal_type": signal_type,
                "positive": positive,
                "negative": negative,
                "win_rate": round((positive / total) * 100.0, 1) if total else 0.0,
                "fail_rate": round((negative / total) * 100.0, 1) if total else 0.0,
            }
        )
    session_signal_quality.sort(
        key=lambda item: (-item["win_rate"], -item["total"], item["session_bucket"], item["signal_type"])
    )

    reason_quality: list[dict[str, Any]] = []
    for reason, card in reason_scorecards.items():
        total = int(card.get("total") or 0)
        positive = int(card.get("positive") or 0)
        negative = int(card.get("negative") or 0)
        reason_quality.append(
            {
                **card,
                "positive_rate": round((positive / total) * 100.0, 1) if total else 0.0,
                "fail_rate": round((negative / total) * 100.0, 1) if total else 0.0,
            }
        )
    reason_quality.sort(key=lambda item: (-item["total"], item["reason"]))

    def _build_daily_trends(
        daily_map: dict[tuple[Any, ...], dict[str, Any]],
        *,
        key_fields: list[str],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in daily_map.values():
            identity = tuple(row.get(field) for field in key_fields)
            grouped.setdefault(identity, []).append(row)
        trends: list[dict[str, Any]] = []
        for identity, rows in grouped.items():
            rows.sort(key=lambda item: str(item.get("date") or ""))
            if len(rows) < 2:
                continue
            current = rows[-1]
            previous = rows[-2]
            current_total = int(current.get("total") or 0)
            previous_total = int(previous.get("total") or 0)
            current_positive = int(current.get("positive") or 0)
            previous_positive = int(previous.get("positive") or 0)
            current_negative = int(current.get("negative") or 0)
            previous_negative = int(previous.get("negative") or 0)
            current_win_rate = round((current_positive / current_total) * 100.0, 1) if current_total else 0.0
            previous_win_rate = round((previous_positive / previous_total) * 100.0, 1) if previous_total else 0.0
            current_fail_rate = round((current_negative / current_total) * 100.0, 1) if current_total else 0.0
            previous_fail_rate = round((previous_negative / previous_total) * 100.0, 1) if previous_total else 0.0
            trends.append(
                {
                    **{field: identity[idx] for idx, field in enumerate(key_fields)},
                    "current_date": current.get("date"),
                    "previous_date": previous.get("date"),
                    "current_total": current_total,
                    "previous_total": previous_total,
                    "current_win_rate": current_win_rate,
                    "previous_win_rate": previous_win_rate,
                    "current_fail_rate": current_fail_rate,
                    "previous_fail_rate": previous_fail_rate,
                    "win_rate_delta": round(current_win_rate - previous_win_rate, 1),
                    "fail_rate_delta": round(current_fail_rate - previous_fail_rate, 1),
                }
            )
        trends.sort(key=lambda item: (-abs(float(item.get("win_rate_delta") or 0.0)), -int(item.get("current_total") or 0)))
        return trends

    reason_trends = _build_daily_trends(reason_daily, key_fields=["reason"])
    session_signal_trends = _build_daily_trends(session_signal_daily, key_fields=["session_bucket", "signal_type"])

    threshold_guidance: list[dict[str, Any]] = []
    for item in reason_quality:
        total = int(item.get("total") or 0)
        positive_rate = float(item.get("positive_rate") or 0.0)
        fail_rate = float(item.get("fail_rate") or 0.0)
        action = "hold"
        rationale = "Not enough evidence to tune this blocker yet."
        confidence = "low"

        if total >= 10:
            confidence = "high"
        elif total >= 5:
            confidence = "medium"

        if total >= 5 and positive_rate >= 60.0 and positive_rate >= fail_rate + 20.0:
            action = "relax_slightly"
            rationale = "This blocker is excluding a meaningful share of setups that later worked."
        elif total >= 5 and fail_rate >= 60.0 and fail_rate >= positive_rate + 20.0:
            action = "tighten"
            rationale = "This blocker is protecting the engine from a large share of failing setups."
        elif total >= 3 and positive_rate >= 35.0 and fail_rate >= 35.0:
            action = "review"
            rationale = "This blocker is mixed. Review nearby thresholds before changing production gates."
        elif total < 3:
            action = "hold"
            rationale = "Sample size is too small to justify a threshold change."

        threshold_guidance.append(
            {
                "reason": item["reason"],
                "action": action,
                "confidence": confidence,
                "sample_size": total,
                "positive_rate": positive_rate,
                "fail_rate": fail_rate,
                "rationale": rationale,
            }
        )
    threshold_guidance.sort(
        key=lambda item: (
            {"tighten": 0, "relax_slightly": 1, "review": 2, "hold": 3}.get(str(item["action"]), 4),
            -int(item["sample_size"]),
            str(item["reason"]),
        )
    )

    return {
        "lookback_hours": hours,
        "counts_by_decision": counts_by_decision,
        "counts_by_stage": stage_counts,
        "top_skip_reasons": top_skip_reasons,
        "sessions": sessions,
        "recent_examples": recent_examples,
        "outcomes_by_label": outcome_counts,
        "false_negatives": false_negatives[:15],
        "false_positives": false_positives[:15],
        "session_quality": session_quality,
        "session_signal_quality": session_signal_quality[:20],
        "conversion": conversion,
        "reason_quality": reason_quality[:25],
        "threshold_guidance": threshold_guidance[:12],
        "reason_trends": reason_trends[:12],
        "session_signal_trends": session_signal_trends[:12],
    }


def get_engine_health_digest(hours: int = 6) -> dict[str, Any]:
    _ensure_schema()
    summary = get_diagnostics_summary(hours=max(1, hours))
    storage = get_learning_storage_status()
    write_config = storage.get("write_config") if isinstance(storage.get("write_config"), dict) else {}
    cutoff = int(time.time()) - max(1, hours) * 3600
    with _connect() as c:
        latest_signal_row = c.execute(
            """
            SELECT token, event_type, alert_ts
            FROM signals
            WHERE alert_ts >= ?
            ORDER BY alert_ts DESC
            LIMIT 1
            """,
            (cutoff,),
        ).fetchone()
        latest_decision_row = c.execute(
            """
            SELECT decision, created_ts, token
            FROM signal_decisions
            WHERE created_ts >= ?
            ORDER BY created_ts DESC
            LIMIT 1
            """,
            (cutoff,),
        ).fetchone()

    counts_by_decision = summary.get("counts_by_decision") if isinstance(summary.get("counts_by_decision"), dict) else {}
    sent_count = sum(int(value or 0) for key, value in counts_by_decision.items() if str(key).endswith("sent"))
    skipped_count = sum(int(value or 0) for key, value in counts_by_decision.items() if "skip" in str(key))
    blocked_count = sum(int(value or 0) for key, value in counts_by_decision.items() if "block" in str(key))
    total_decisions = sum(int(value or 0) for value in counts_by_decision.values())

    skip_pressure = round((skipped_count / total_decisions) * 100.0, 1) if total_decisions else 0.0
    block_pressure = round((blocked_count / total_decisions) * 100.0, 1) if total_decisions else 0.0
    send_rate = round((sent_count / total_decisions) * 100.0, 1) if total_decisions else 0.0

    latest_signal = None
    if latest_signal_row:
        latest_signal = {
            "token": latest_signal_row[0],
            "event_type": latest_signal_row[1],
            "alert_ts": latest_signal_row[2],
            "age_minutes": round(max(0.0, (time.time() - int(latest_signal_row[2] or 0)) / 60.0), 1),
        }

    latest_decision = None
    if latest_decision_row:
        latest_decision = {
            "decision": latest_decision_row[0],
            "created_ts": latest_decision_row[1],
            "token": latest_decision_row[2],
            "age_minutes": round(max(0.0, (time.time() - int(latest_decision_row[1] or 0)) / 60.0), 1),
        }

    status = "quiet"
    status_detail = "The engine is processing, but recent sends are sparse."
    if total_decisions == 0 and latest_signal is None:
        status = "cold"
        status_detail = "No recent decision or alert activity in the selected window."
    elif total_decisions > 0 and sent_count == 0 and skip_pressure >= 70.0:
        status = "gated"
        status_detail = "The engine is active, but gates are filtering most setups."
    elif total_decisions > 0 and sent_count == 0 and block_pressure >= 50.0:
        status = "blocked"
        status_detail = "The engine is active, but promotion blockers are suppressing sends."
    elif sent_count > 0:
        status = "active"
        status_detail = "The engine is active and sending alerts in the selected window."
    elif latest_decision and latest_decision["age_minutes"] <= 15.0:
        status = "processing"
        status_detail = "The engine is processing fresh events, but none qualified for send yet."
    if total_decisions == 0 and int(storage.get("decision_count") or 0) == 0 and int(storage.get("signal_count") or 0) == 0:
        status_detail = "No learning rows exist in the current engine DB yet. This usually means the worker has not written here or both services are not sharing the same DB path."
        if write_config.get("process_role") == "engine" and not write_config.get("shared_db_env_set") and not write_config.get("remote_enabled"):
            status_detail += " Engine is in local-only write mode with no shared DB path configured."
        elif write_config.get("process_role") == "worker" and write_config.get("mode") == "remote" and not write_config.get("remote_base_url"):
            status_detail += " Worker remote write mode is enabled but no remote base URL is configured."

    top_skip_reasons = summary.get("top_skip_reasons") if isinstance(summary.get("top_skip_reasons"), list) else []
    top_reasons = top_skip_reasons[:5]

    return {
        "lookback_hours": hours,
        "status": status,
        "status_detail": status_detail,
        "sent_count": sent_count,
        "skipped_count": skipped_count,
        "blocked_count": blocked_count,
        "total_decisions": total_decisions,
        "send_rate": send_rate,
        "skip_pressure": skip_pressure,
        "block_pressure": block_pressure,
        "latest_signal": latest_signal,
        "latest_decision": latest_decision,
        "top_skip_reasons": top_reasons,
        "storage": storage,
        "write_config": write_config,
    }


def render_engine_health_html(hours: int = 6) -> str:
    digest = get_engine_health_digest(hours=max(1, hours))
    latest_signal = digest.get("latest_signal") if isinstance(digest.get("latest_signal"), dict) else {}
    latest_decision = digest.get("latest_decision") if isinstance(digest.get("latest_decision"), dict) else {}
    top_skip_reasons = digest.get("top_skip_reasons") if isinstance(digest.get("top_skip_reasons"), list) else []
    storage = digest.get("storage") if isinstance(digest.get("storage"), dict) else {}

    def metric_card(label: str, value: Any) -> str:
        return (
            '<div class="metric-card">'
            f'<span>{html.escape(label)}</span>'
            f"<strong>{html.escape(str(value))}</strong>"
            "</div>"
        )

    top_reason_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{int(item.get('count') or 0)}</td>"
        "</tr>"
        for item in top_skip_reasons
    ) or "<tr><td colspan='2'>No skip reasons in this window.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Engine Health</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --panel-2: rgba(18, 34, 52, 0.96);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1200px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 20px; padding: 18px; }}
    .grid {{ display:grid; gap:16px; grid-template-columns: repeat(4, minmax(0,1fr)); margin-top: 18px; }}
    .wide-grid {{ display:grid; gap:16px; grid-template-columns: 1fr 1fr; margin-top: 18px; }}
    .metric-card {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 18px; padding: 14px 16px; }}
    .metric-card span {{ display:block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .12em; margin-bottom: 6px; }}
    .metric-card strong {{ font-size: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 32px; }}
    h2 {{ margin: 0 0 14px; font-size: 14px; letter-spacing: .12em; color: var(--muted); text-transform: uppercase; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    @media (max-width: 1020px) {{
      .grid, .wide-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Engine Health</h1>
      <p>{html.escape(str(digest.get("status_detail") or ""))}</p>
    </section>
    <div class="grid">
      {metric_card("Status", digest.get("status", "unknown"))}
      {metric_card("Sent", digest.get("sent_count", 0))}
      {metric_card("Skip Pressure", f"{digest.get('skip_pressure', 0)}%")}
      {metric_card("Block Pressure", f"{digest.get('block_pressure', 0)}%")}
    </div>
    <div class="grid">
      {metric_card("DB Path", storage.get("db_path", "unknown"))}
      {metric_card("Signals", storage.get("signal_count", 0))}
      {metric_card("Decisions", storage.get("decision_count", 0))}
      {metric_card("Snapshots", storage.get("snapshot_count", 0))}
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Latest Alert</h2>
        <table>
          <tbody>
            <tr><th>Token</th><td>{html.escape(str(latest_signal.get("token") or "None"))}</td></tr>
            <tr><th>Type</th><td>{html.escape(str(latest_signal.get("event_type") or "None"))}</td></tr>
            <tr><th>Age Minutes</th><td>{html.escape(str(latest_signal.get("age_minutes") or "N/A"))}</td></tr>
          </tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Latest Decision</h2>
        <table>
          <tbody>
            <tr><th>Token</th><td>{html.escape(str(latest_decision.get("token") or "None"))}</td></tr>
            <tr><th>Decision</th><td>{html.escape(str(latest_decision.get("decision") or "None"))}</td></tr>
            <tr><th>Age Minutes</th><td>{html.escape(str(latest_decision.get("age_minutes") or "N/A"))}</td></tr>
          </tbody>
        </table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Top Skip Reasons</h2>
        <table>
          <thead><tr><th>Reason</th><th>Count</th></tr></thead>
          <tbody>{top_reason_rows}</tbody>
        </table>
      </section>
    </div>
  </div>
</body>
</html>"""


def get_diagnostics_recommendations(hours: int = 24) -> list[dict[str, str]]:
    summary = get_diagnostics_summary(hours)
    recs: list[dict[str, str]] = []
    top_reasons = summary.get("top_skip_reasons") or []
    counts_by_decision = summary.get("counts_by_decision") or {}
    sessions = summary.get("sessions") or {}
    false_negatives = summary.get("false_negatives") or []
    false_positives = summary.get("false_positives") or []
    session_quality = summary.get("session_quality") or []
    session_signal_quality = summary.get("session_signal_quality") or []
    conversion = summary.get("conversion") or {}
    reason_quality = summary.get("reason_quality") or []
    threshold_guidance = summary.get("threshold_guidance") or []
    reason_trends = summary.get("reason_trends") or []
    session_signal_trends = summary.get("session_signal_trends") or []

    if counts_by_decision.get("candidate_gate_skip", 0) > 0:
        recs.append(
            {
                "title": "Candidate Gate Pressure",
                "detail": "Candidate skips are active. Review the top skip reasons before lowering thresholds.",
            }
        )
    if any(str(item.get("reason") or "").startswith("age<") for item in top_reasons):
        recs.append(
            {
                "title": "Age Gate Dominance",
                "detail": "A large share of candidates are being filtered by token age. Check whether your market is moving faster than the current age floor.",
            }
        )
    if any("dex_gate:liq<" in str(item.get("reason") or "") for item in top_reasons):
        recs.append(
            {
                "title": "Liquidity Bottleneck",
                "detail": "Liquidity is a major blocker. Inspect whether this is protecting quality or causing missed early movers.",
            }
        )
    if any("buyers_low" == str(item.get("reason") or "") for item in top_reasons):
        recs.append(
            {
                "title": "Promotion Breadth Bottleneck",
                "detail": "Promotions are often blocked by 15m buyer breadth. Compare those blocks against later winners before changing the threshold.",
            }
        )
    if false_negatives:
        top_reason = None
        reason_counts: dict[str, int] = {}
        for item in false_negatives:
            for reason in item.get("reasons") or []:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if reason_counts:
            top_reason = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        recs.append(
            {
                "title": "False Negatives Detected",
                "detail": (
                    f"{len(false_negatives)} skipped or blocked setups later worked. "
                    + (f"The most common blocker was `{top_reason}`." if top_reason else "Inspect blockers before tightening further.")
                ),
            }
        )
    if false_positives:
        recs.append(
            {
                "title": "False Positive Review",
                "detail": f"{len(false_positives)} recent sent alerts later faded or failed. Audit these before relaxing risk or breadth gates.",
            }
        )
    if reason_quality:
        costly_reason = max(reason_quality, key=lambda item: (item["positive"], -item["negative"], item["total"]))
        protective_reason = max(reason_quality, key=lambda item: (item["negative"], -item["positive"], item["total"]))
        if int(costly_reason.get("positive") or 0) > 0:
            recs.append(
                {
                    "title": "Most Costly Blocker",
                    "detail": (
                        f"`{costly_reason['reason']}` is blocking winners: "
                        f"{costly_reason['positive']}/{costly_reason['total']} later produced positive outcomes."
                    ),
                }
            )
        if int(protective_reason.get("negative") or 0) > 0:
            recs.append(
                {
                    "title": "Most Protective Blocker",
                    "detail": (
                        f"`{protective_reason['reason']}` is filtering weak setups: "
                        f"{protective_reason['negative']}/{protective_reason['total']} later failed or faded."
                    ),
                }
            )
    for guidance in threshold_guidance[:3]:
        action_text = {
            "tighten": "Tighten",
            "relax_slightly": "Relax slightly",
            "review": "Review",
            "hold": "Hold",
        }.get(str(guidance.get("action") or "hold"), "Hold")
        recs.append(
            {
                "title": f"Threshold: {guidance.get('reason')}",
                "detail": (
                    f"{action_text} with {guidance.get('confidence')} confidence. "
                    f"Sample={int(guidance.get('sample_size') or 0)}, "
                    f"positive={guidance.get('positive_rate')}%, fail={guidance.get('fail_rate')}%."
                ),
            }
        )

    best_session = None
    best_sent = -1
    for session_name, stats in sessions.items():
        sent = int(stats.get("sent") or 0)
        if sent > best_sent:
            best_sent = sent
            best_session = session_name
    if best_session:
        recs.append(
            {
                "title": "Most Active Session",
                "detail": f"The busiest recent session is `{best_session}`. Cross-check this against outcome quality before biasing the engine toward it.",
            }
        )
    if session_quality:
        best_quality = session_quality[0]
        worst_quality = sorted(session_quality, key=lambda item: (item["win_rate"], -item["total"], item["session_bucket"]))[0]
        recs.append(
            {
                "title": "Session Quality Spread",
                "detail": (
                    f"Best realized session is `{best_quality['session_bucket']}` at {best_quality['win_rate']}% positive outcomes. "
                    f"Weakest is `{worst_quality['session_bucket']}` at {worst_quality['win_rate']}%."
                ),
            }
        )
    if session_signal_quality:
        best_combo = session_signal_quality[0]
        worst_combo = sorted(
            session_signal_quality,
            key=lambda item: (item["win_rate"], -item["total"], item["session_bucket"], item["signal_type"]),
        )[0]
        recs.append(
            {
                "title": "Best Session x Signal",
                "detail": (
                    f"`{best_combo['signal_type']}` in `{best_combo['session_bucket']}` is leading at "
                    f"{best_combo['win_rate']}% positive outcomes across {best_combo['total']} samples."
                ),
            }
        )
    if reason_trends:
        improving_reason = max(reason_trends, key=lambda item: (item["win_rate_delta"], item["current_total"]))
        degrading_reason = min(reason_trends, key=lambda item: (item["win_rate_delta"], -item["current_total"]))
        if float(improving_reason.get("win_rate_delta") or 0.0) > 0:
            recs.append(
                {
                    "title": "Improving Blocker Trend",
                    "detail": (
                        f"`{improving_reason['reason']}` improved by {improving_reason['win_rate_delta']} pts "
                        f"vs {improving_reason['previous_date']}."
                    ),
                }
            )
        if float(degrading_reason.get("win_rate_delta") or 0.0) < 0:
            recs.append(
                {
                    "title": "Degrading Blocker Trend",
                    "detail": (
                        f"`{degrading_reason['reason']}` dropped by {abs(float(degrading_reason['win_rate_delta'] or 0.0))} pts "
                        f"vs {degrading_reason['previous_date']}."
                    ),
                }
            )
    if session_signal_quality:
        recs.append(
            {
                "title": "Weakest Session x Signal",
                "detail": (
                    f"`{worst_combo['signal_type']}` in `{worst_combo['session_bucket']}` is weakest at "
                    f"{worst_combo['win_rate']}% positive outcomes across {worst_combo['total']} samples."
                ),
            }
        )
    if session_signal_trends:
        improving_combo = max(session_signal_trends, key=lambda item: (item["win_rate_delta"], item["current_total"]))
        degrading_combo = min(session_signal_trends, key=lambda item: (item["win_rate_delta"], -item["current_total"]))
        if float(improving_combo.get("win_rate_delta") or 0.0) > 0:
            recs.append(
                {
                    "title": "Improving Session x Signal",
                    "detail": (
                        f"`{improving_combo['signal_type']}` in `{improving_combo['session_bucket']}` improved by "
                        f"{improving_combo['win_rate_delta']} pts day over day."
                    ),
                }
            )
        if float(degrading_combo.get("win_rate_delta") or 0.0) < 0:
            recs.append(
                {
                    "title": "Degrading Session x Signal",
                    "detail": (
                        f"`{degrading_combo['signal_type']}` in `{degrading_combo['session_bucket']}` fell by "
                        f"{abs(float(degrading_combo['win_rate_delta'] or 0.0))} pts day over day."
                    ),
                }
            )
    if int(conversion.get("candidate_tokens") or 0) > 0:
        conv_rate = round(
            (int(conversion.get("candidate_to_promoted_tokens") or 0) / max(1, int(conversion.get("candidate_tokens") or 0))) * 100.0,
            1,
        )
        recs.append(
            {
                "title": "Candidate Conversion",
                "detail": f"{conv_rate}% of candidate tokens converted into promoted tokens in the lookback window.",
            }
        )

    if not recs:
        recs.append(
            {
                "title": "No Strong Pattern Yet",
                "detail": "Collect more decision telemetry before changing gates. The recent sample is too small to justify tuning.",
            }
        )
    return recs


def get_live_validation_records(
    *,
    hours: int = 72,
    limit: int = 200,
    route_class: str | None = None,
    sent_only: bool = False,
) -> list[dict[str, Any]]:
    _ensure_schema()
    cutoff = int(time.time()) - max(1, hours) * 3600
    with _connect() as c:
        signal_rows = c.execute(
            """
            SELECT signal_id, token, event_type, source, creator, alert_ts, updated_ts, external_ref,
                   lifecycle, confidence_score, attention_score, risk_score, elite_score,
                   market_cap_usd, liquidity_usd, volume_m5_usd, age_minutes, payload_json
            FROM signals
            WHERE alert_ts >= ?
            ORDER BY alert_ts DESC
            """,
            (cutoff,),
        ).fetchall()
        decision_rows = c.execute(
            """
            SELECT decision_id, signal_id, token, event_type, stage, decision, action_taken,
                   policy_name, policy_version, reasons_json, features_json,
                   attention_score, risk_score, confidence_score, creator_score,
                   lifecycle, created_ts
            FROM signal_decisions
            WHERE created_ts >= ?
            ORDER BY created_ts DESC
            """,
            (cutoff,),
        ).fetchall()
        snapshot_rows = c.execute(
            """
            SELECT signal_id, horizon_minutes, captured_ts, outcome_label, market_cap_change_pct,
                   liquidity_change_pct, volume_m5_change_pct, snapshot_json
            FROM signal_snapshots
            WHERE signal_id IN (SELECT signal_id FROM signals WHERE alert_ts >= ?)
            ORDER BY horizon_minutes DESC, captured_ts DESC
            """,
            (cutoff,),
        ).fetchall()

    latest_decision_by_signal: dict[str, dict[str, Any]] = {}
    for row in decision_rows:
        signal_id = str(row[1] or "")
        if not signal_id or signal_id in latest_decision_by_signal:
            continue
        latest_decision_by_signal[signal_id] = {
            "decision_id": row[0],
            "signal_id": signal_id,
            "token": row[2],
            "event_type": row[3],
            "stage": row[4],
            "decision": row[5],
            "action_taken": row[6],
            "policy_name": row[7],
            "policy_version": row[8],
            "reasons": [str(item) for item in _json_loads_list(row[9])],
            "features": _json_loads_dict(row[10]),
            "attention_score": row[11],
            "risk_score": row[12],
            "confidence_score": row[13],
            "creator_score": row[14],
            "lifecycle": row[15],
            "created_ts": row[16],
        }

    latest_snapshot_by_signal: dict[str, dict[str, Any]] = {}
    for row in snapshot_rows:
        signal_id = str(row[0] or "")
        if not signal_id or signal_id in latest_snapshot_by_signal:
            continue
        latest_snapshot_by_signal[signal_id] = {
            "signal_id": signal_id,
            "horizon_minutes": row[1],
            "captured_ts": row[2],
            "outcome_label": row[3],
            "market_cap_change_pct": row[4],
            "liquidity_change_pct": row[5],
            "volume_m5_change_pct": row[6],
            "snapshot": _json_loads_dict(row[7]),
        }

    records: list[dict[str, Any]] = []
    wanted_route = str(route_class or "").strip().lower() or None
    for row in signal_rows:
        signal_id = str(row[0] or "")
        decision = latest_decision_by_signal.get(signal_id, {})
        features = decision.get("features") if isinstance(decision.get("features"), dict) else {}
        snapshot = latest_snapshot_by_signal.get(signal_id, {})
        source = str(row[3] or "")
        payload = _json_loads_dict(row[17])
        sent_to_discord = bool(payload) or bool(row[7])
        derived_route = _decision_route_class(
            signal_event_type=row[2],
            stage=decision.get("stage"),
            decision=decision.get("decision"),
            action_taken=decision.get("action_taken"),
            features=features,
        )
        if wanted_route and derived_route != wanted_route:
            continue
        if sent_only and not sent_to_discord:
            continue
        policy_descriptor = _policy_descriptor_from_features(
            features,
            policy_name=decision.get("policy_name"),
            policy_version=decision.get("policy_version"),
        )
        record = {
            "signal_id": signal_id,
            "timestamp": row[5],
            "token": row[1],
            "creator": row[4],
            "event_type": row[2],
            "final_route_class": derived_route,
            "sent_to_discord": sent_to_discord,
            "source": source,
            "external_ref": row[7],
            "score": row[9],
            "confidence": row[9],
            "attention_score": row[10],
            "risk_score": row[11],
            "elite_score": row[12],
            "market_cap_usd": row[13],
            "liquidity_usd": row[14],
            "volume_m5_usd": row[15],
            "age_minutes": row[16],
            "decision_stage": decision.get("stage"),
            "decision_name": decision.get("decision"),
            "action_taken": decision.get("action_taken"),
            "decision_reasons": decision.get("reasons") or [],
            "key_metrics": {
                "attention_score": row[10],
                "risk_score": row[11],
                "elite_score": row[12],
                "market_cap_usd": row[13],
                "liquidity_usd": row[14],
                "volume_m5_usd": row[15],
                "age_minutes": row[16],
                "tracked_wallet_hits": features.get("tracked_wallet_hits"),
                "kol_wallet_hits": features.get("kol_wallet_hits"),
                "unique_10s": features.get("unique_10s"),
                "burst_10s": features.get("burst_10s"),
                "unique_buyers_5m": features.get("unique_buyers_5m"),
                "top_wallet_share_30s": features.get("top_wallet_share_30s"),
                "route_confidence": features.get("route_confidence"),
            },
            "thresholds_used": policy_descriptor,
            "parameter_fingerprint": str(features.get("parameter_fingerprint") or _policy_config_fingerprint(policy_descriptor)),
            "policy_name": decision.get("policy_name"),
            "policy_version": decision.get("policy_version"),
            "bypasses_used": _extract_bypass_flags(features),
            "route_tier": str(features.get("route_tier") or "").strip().lower() or None,
            "route_confirmations": features.get("route_confirmations") if isinstance(features.get("route_confirmations"), list) else [],
            "route_blockers": features.get("route_blockers") if isinstance(features.get("route_blockers"), list) else [],
            "sniper_blockers": features.get("sniper_blockers") if isinstance(features.get("sniper_blockers"), list) else [],
            "outcome_label": snapshot.get("outcome_label"),
            "outcome_market_cap_change_pct": snapshot.get("market_cap_change_pct"),
            "outcome_liquidity_change_pct": snapshot.get("liquidity_change_pct"),
            "outcome_volume_m5_change_pct": snapshot.get("volume_m5_change_pct"),
            "outcome_horizon_minutes": snapshot.get("horizon_minutes"),
            "outcome_snapshot": snapshot.get("snapshot") if isinstance(snapshot.get("snapshot"), dict) else {},
        }
        record["evaluation_bucket"] = _classify_validation_bucket(
            route_class=derived_route,
            sent_to_discord=sent_to_discord,
            outcome_label=str(snapshot.get("outcome_label") or "pending"),
            market_cap_change_pct=_to_float(snapshot.get("market_cap_change_pct")),
            age_minutes=_to_float(row[16]),
        )
        records.append(record)
        if len(records) >= max(1, limit):
            break
    return records


def get_missed_runner_analysis(*, hours: int = 168, limit: int = 50) -> dict[str, Any]:
    records = get_live_validation_records(hours=hours, limit=max(limit * 5, 200), sent_only=False)
    missed: list[dict[str, Any]] = []
    for record in records:
        if record.get("sent_to_discord"):
            continue
        outcome_label = str(record.get("outcome_label") or "pending")
        if outcome_label not in _POSITIVE_OUTCOME_LABELS:
            continue
        reasons = [str(item) for item in record.get("decision_reasons") or []]
        route_class = str(record.get("final_route_class") or "unknown")
        route_blockers = [str(item) for item in record.get("route_blockers") or []]
        sniper_blockers = [str(item) for item in record.get("sniper_blockers") or []]
        missed.append(
            {
                "signal_id": record.get("signal_id"),
                "token": record.get("token"),
                "timestamp": record.get("timestamp"),
                "decision_stage": record.get("decision_stage"),
                "decision_name": record.get("decision_name"),
                "binding_reasons": reasons[:8],
                "binding_gate_families": sorted({_reason_family(reason) for reason in reasons}),
                "route_class": route_class,
                "miss_bucket": "missed_sniper" if route_class == "sniper" or sniper_blockers else "missed_runner",
                "outcome_label": outcome_label,
                "market_cap_change_pct": record.get("outcome_market_cap_change_pct"),
                "route_blockers": route_blockers[:6],
                "sniper_blockers": sniper_blockers[:6],
                "thresholds_used": record.get("thresholds_used") if isinstance(record.get("thresholds_used"), dict) else {},
            }
        )
    missed.sort(
        key=lambda item: (
            0 if str(item.get("miss_bucket") or "") == "missed_sniper" else 1,
            -float(item.get("market_cap_change_pct") or 0.0),
            str(item.get("token") or ""),
        )
    )
    family_counts: dict[str, int] = {}
    for item in missed:
        for family in item.get("binding_gate_families") or []:
            family_counts[family] = family_counts.get(family, 0) + 1
    return {
        "lookback_hours": hours,
        "missed_runner_count": len(missed),
        "top_binding_gate_families": [
            {"family": family, "count": count}
            for family, count in sorted(family_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
        ],
        "missed_runners": missed[: max(1, limit)],
    }


def get_policy_validation_comparison(*, hours: int = 168, limit: int = 12) -> dict[str, Any]:
    records = get_live_validation_records(hours=hours, limit=5000, sent_only=False)
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        descriptor = record.get("thresholds_used") if isinstance(record.get("thresholds_used"), dict) else {}
        fingerprint = str(record.get("parameter_fingerprint") or _policy_config_fingerprint(descriptor))
        policy_key = f"{record.get('policy_name') or 'unknown'}@{record.get('policy_version') or 'unknown'}"
        group = grouped.setdefault(
            f"{policy_key}|{fingerprint}",
            {
                "parameter_fingerprint": fingerprint,
                "policy_name": record.get("policy_name"),
                "policy_version": record.get("policy_version"),
                "policy_key": policy_key,
                "thresholds_used": descriptor,
                "total": 0,
                "sent": 0,
                "positive": 0,
                "negative": 0,
                "pending": 0,
                "route_counts": {},
                "evaluation_buckets": {},
            },
        )
        group["total"] += 1
        if record.get("sent_to_discord"):
            group["sent"] += 1
        outcome_label = str(record.get("outcome_label") or "pending")
        if outcome_label in _POSITIVE_OUTCOME_LABELS:
            group["positive"] += 1
        elif outcome_label in _NEGATIVE_OUTCOME_LABELS:
            group["negative"] += 1
        else:
            group["pending"] += 1
        route = str(record.get("final_route_class") or "unknown")
        group["route_counts"][route] = int(group["route_counts"].get(route, 0)) + 1
        bucket = str(record.get("evaluation_bucket") or "unknown")
        group["evaluation_buckets"][bucket] = int(group["evaluation_buckets"].get(bucket, 0)) + 1

    ranked: list[dict[str, Any]] = []
    for item in grouped.values():
        sent = int(item["sent"] or 0)
        positive = int(item["positive"] or 0)
        negative = int(item["negative"] or 0)
        total = int(item["total"] or 0)
        precision = round((positive / sent) * 100.0, 1) if sent else 0.0
        false_positive_rate = round((negative / sent) * 100.0, 1) if sent else 0.0
        coverage = round((sent / total) * 100.0, 1) if total else 0.0
        robustness = round(precision - (false_positive_rate * 0.6), 1)
        ranked.append(
            {
                **item,
                "precision": precision,
                "false_positive_rate": false_positive_rate,
                "coverage": coverage,
                "robustness": robustness,
            }
        )
    ranked.sort(
        key=lambda item: (
            -float(item.get("robustness") or 0.0),
            -float(item.get("precision") or 0.0),
            int(item.get("sent") or 0),
            str(item.get("policy_key") or ""),
        )
    )
    return {
        "lookback_hours": hours,
        "variant_count": len(ranked),
        "variants": ranked[: max(1, limit)],
    }


def get_live_validation_summary(*, hours: int = 72, limit: int = 200) -> dict[str, Any]:
    records = get_live_validation_records(hours=hours, limit=max(limit, 300), sent_only=False)
    missed = get_missed_runner_analysis(hours=hours, limit=max(10, min(50, limit)))
    comparison = get_policy_validation_comparison(hours=max(hours, 24), limit=8)

    route_counts: dict[str, int] = {}
    sent_route_counts: dict[str, int] = {}
    evaluation_buckets: dict[str, int] = {}
    gate_families: dict[str, dict[str, Any]] = {}
    route_quality_map: dict[str, dict[str, Any]] = {}
    borderline_cases: list[dict[str, Any]] = []

    for record in records:
        route = str(record.get("final_route_class") or "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1
        if record.get("sent_to_discord"):
            sent_route_counts[route] = sent_route_counts.get(route, 0) + 1
        bucket = str(record.get("evaluation_bucket") or "unknown")
        evaluation_buckets[bucket] = evaluation_buckets.get(bucket, 0) + 1

        quality = route_quality_map.setdefault(route, {"route_class": route, "total": 0, "sent": 0, "positive": 0, "negative": 0})
        quality["total"] += 1
        if record.get("sent_to_discord"):
            quality["sent"] += 1
        outcome = str(record.get("outcome_label") or "pending")
        if outcome in _POSITIVE_OUTCOME_LABELS:
            quality["positive"] += 1
        elif outcome in _NEGATIVE_OUTCOME_LABELS:
            quality["negative"] += 1

        reasons = [str(item) for item in record.get("decision_reasons") or []]
        for reason in reasons:
            family = _reason_family(reason)
            family_card = gate_families.setdefault(family, {"family": family, "count": 0, "positive": 0, "negative": 0})
            family_card["count"] += 1
            if outcome in _POSITIVE_OUTCOME_LABELS:
                family_card["positive"] += 1
            elif outcome in _NEGATIVE_OUTCOME_LABELS:
                family_card["negative"] += 1

        if (
            bucket in {"too_early_but_valid", "weak_alert", "pending"}
            or (not record.get("sent_to_discord") and outcome in _POSITIVE_OUTCOME_LABELS)
            or (record.get("sent_to_discord") and outcome in _NEGATIVE_OUTCOME_LABELS and route in {"sniper", "promoted"})
        ):
            borderline_cases.append(
                {
                    "token": record.get("token"),
                    "route_class": route,
                    "sent_to_discord": record.get("sent_to_discord"),
                    "evaluation_bucket": bucket,
                    "outcome_label": outcome,
                    "decision_name": record.get("decision_name"),
                    "decision_reasons": reasons[:6],
                    "market_cap_change_pct": record.get("outcome_market_cap_change_pct"),
                }
            )

    route_quality: list[dict[str, Any]] = []
    for item in route_quality_map.values():
        sent = int(item["sent"] or 0)
        positive = int(item["positive"] or 0)
        negative = int(item["negative"] or 0)
        route_quality.append(
            {
                **item,
                "precision": round((positive / sent) * 100.0, 1) if sent else 0.0,
                "false_positive_rate": round((negative / sent) * 100.0, 1) if sent else 0.0,
                "conversion_rate": round((positive / max(1, int(item["total"] or 0))) * 100.0, 1),
            }
        )
    route_quality.sort(key=lambda item: (-float(item["precision"]), -int(item["sent"]), item["route_class"]))

    gate_family_summary: list[dict[str, Any]] = []
    for item in gate_families.values():
        count = int(item["count"] or 0)
        positive = int(item["positive"] or 0)
        negative = int(item["negative"] or 0)
        gate_family_summary.append(
            {
                **item,
                "positive_rate": round((positive / count) * 100.0, 1) if count else 0.0,
                "negative_rate": round((negative / count) * 100.0, 1) if count else 0.0,
            }
        )
    gate_family_summary.sort(key=lambda item: (-int(item["count"]), item["family"]))
    borderline_cases.sort(
        key=lambda item: (
            0 if str(item.get("evaluation_bucket") or "") == "missed_sniper" else 1,
            -float(item.get("market_cap_change_pct") or 0.0),
            str(item.get("token") or ""),
        )
    )

    diagnostics = get_diagnostics_summary(hours=hours)
    sent_records = [record for record in records if record.get("sent_to_discord")]
    return {
        "lookback_hours": hours,
        "total_tracked_opportunities": len(records),
        "sent_alerts": len(sent_records),
        "route_counts": route_counts,
        "sent_route_counts": sent_route_counts,
        "evaluation_buckets": evaluation_buckets,
        "route_quality": route_quality,
        "gate_family_summary": gate_family_summary[:10],
        "missed_runner_analysis": missed,
        "policy_comparison": comparison,
        "borderline_cases": borderline_cases[:12],
        "threshold_effectiveness": diagnostics.get("threshold_guidance") if isinstance(diagnostics.get("threshold_guidance"), list) else [],
        "reject_reasons": diagnostics.get("top_skip_reasons") if isinstance(diagnostics.get("top_skip_reasons"), list) else [],
        "alerts": sent_records[: max(1, min(limit, 100))],
        "opportunities": records[: max(1, limit)],
        "tuning_workflow": {
            "parameter_source": "worker/signal_policy.py and env-backed policy thresholds",
            "test_path": "app/services/parameter_search_service.py",
            "compare_path": "/learning/validation/policies and /learning/policy/replay/*",
            "safe_rollout": "shadow or replay first, then tuning approval, then rollout verification",
            "overfitting_guard": "prefer multi-day comparisons, keep sample-size discipline, and do not promote a parameter set on one regime burst alone",
        },
    }


def render_live_validation_html(*, hours: int = 72, limit: int = 200) -> str:
    summary = get_live_validation_summary(hours=hours, limit=limit)
    route_quality = summary.get("route_quality") if isinstance(summary.get("route_quality"), list) else []
    gate_families = summary.get("gate_family_summary") if isinstance(summary.get("gate_family_summary"), list) else []
    borderline_cases = summary.get("borderline_cases") if isinstance(summary.get("borderline_cases"), list) else []
    threshold_effectiveness = summary.get("threshold_effectiveness") if isinstance(summary.get("threshold_effectiveness"), list) else []
    missed = summary.get("missed_runner_analysis") if isinstance(summary.get("missed_runner_analysis"), dict) else {}
    variants = (summary.get("policy_comparison") or {}).get("variants") if isinstance(summary.get("policy_comparison"), dict) else []

    def metric_card(label: str, value: Any) -> str:
        return (
            '<div class="metric-card">'
            f'<span>{html.escape(label)}</span>'
            f"<strong>{html.escape(str(value))}</strong>"
            "</div>"
        )

    overview_cards = "".join(
        [
            metric_card("Tracked", summary.get("total_tracked_opportunities", 0)),
            metric_card("Sent Alerts", summary.get("sent_alerts", 0)),
            metric_card("Missed Runners", missed.get("missed_runner_count", 0)),
            metric_card("Variants", (summary.get("policy_comparison") or {}).get("variant_count", 0)),
        ]
    )

    route_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('route_class') or 'unknown'))}</td>"
        f"<td>{int(item.get('sent') or 0)}</td>"
        f"<td>{html.escape(str(item.get('precision') or 0))}%</td>"
        f"<td>{html.escape(str(item.get('false_positive_rate') or 0))}%</td>"
        f"<td>{html.escape(str(item.get('conversion_rate') or 0))}%</td>"
        "</tr>"
        for item in route_quality[:8]
    ) or "<tr><td colspan='5'>No route data</td></tr>"

    family_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('family') or 'unknown'))}</td>"
        f"<td>{int(item.get('count') or 0)}</td>"
        f"<td>{html.escape(str(item.get('positive_rate') or 0))}%</td>"
        f"<td>{html.escape(str(item.get('negative_rate') or 0))}%</td>"
        "</tr>"
        for item in gate_families[:10]
    ) or "<tr><td colspan='4'>No gate-family data</td></tr>"

    borderline_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('token') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('route_class') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('evaluation_bucket') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('outcome_label') or 'pending'))}</td>"
        "</tr>"
        for item in borderline_cases[:10]
    ) or "<tr><td colspan='4'>No borderline cases</td></tr>"

    threshold_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('action') or 'hold'))}</td>"
        f"<td>{html.escape(str(item.get('confidence') or 'low'))}</td>"
        f"<td>{int(item.get('sample_size') or 0)}</td>"
        "</tr>"
        for item in threshold_effectiveness[:10]
    ) or "<tr><td colspan='4'>No threshold guidance</td></tr>"

    missed_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('token') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('miss_bucket') or 'missed_runner'))}</td>"
        f"<td>{html.escape(str(item.get('decision_name') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('market_cap_change_pct') or '-'))}</td>"
        "</tr>"
        for item in (missed.get("missed_runners") or [])[:10]
    ) or "<tr><td colspan='4'>No missed runners</td></tr>"

    variant_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('policy_key') or 'unknown'))}</td>"
        f"<td>{int(item.get('sent') or 0)}</td>"
        f"<td>{html.escape(str(item.get('precision') or 0))}%</td>"
        f"<td>{html.escape(str(item.get('false_positive_rate') or 0))}%</td>"
        f"<td>{html.escape(str(item.get('coverage') or 0))}%</td>"
        "</tr>"
        for item in variants[:8]
    ) or "<tr><td colspan='5'>No variant comparison data</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live Validation Dashboard</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --panel-2: rgba(18, 34, 52, 0.96);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(230, 167, 74, .12), transparent 26%),
        radial-gradient(circle at bottom right, rgba(46, 111, 201, .18), transparent 28%),
        linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1380px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; box-shadow: 0 18px 50px rgba(0,0,0,.35); }}
    .grid {{ display:grid; gap:16px; grid-template-columns: repeat(4, minmax(0,1fr)); margin-top: 18px; }}
    .wide-grid {{ display:grid; gap:16px; grid-template-columns: 1fr 1fr; margin-top: 18px; }}
    .metric-card {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 18px; padding: 14px 16px; }}
    .metric-card span {{ display:block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .12em; margin-bottom: 6px; }}
    .metric-card strong {{ font-size: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 32px; }}
    h2 {{ margin: 0 0 14px; font-size: 14px; letter-spacing: .12em; color: var(--muted); text-transform: uppercase; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    @media (max-width: 1020px) {{
      .grid, .wide-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Live Validation Dashboard</h1>
      <p>Post-optimization validation view across routed opportunities, missed runners, threshold pressure, and policy variants over the last {int(summary.get("lookback_hours") or 0)} hours.</p>
    </section>
    <div class="grid">{overview_cards}</div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Route Quality</h2>
        <table><thead><tr><th>Route</th><th>Sent</th><th>Precision</th><th>False Positives</th><th>Conversion</th></tr></thead><tbody>{route_rows}</tbody></table>
      </section>
      <section class="panel">
        <h2>Gate Families</h2>
        <table><thead><tr><th>Family</th><th>Count</th><th>Positive Rate</th><th>Negative Rate</th></tr></thead><tbody>{family_rows}</tbody></table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Missed Runners</h2>
        <table><thead><tr><th>Token</th><th>Bucket</th><th>Decision</th><th>MC %</th></tr></thead><tbody>{missed_rows}</tbody></table>
      </section>
      <section class="panel">
        <h2>Policy Variants</h2>
        <table><thead><tr><th>Policy</th><th>Sent</th><th>Precision</th><th>False Positives</th><th>Coverage</th></tr></thead><tbody>{variant_rows}</tbody></table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Threshold Effectiveness</h2>
        <table><thead><tr><th>Reason</th><th>Action</th><th>Confidence</th><th>Sample</th></tr></thead><tbody>{threshold_rows}</tbody></table>
      </section>
      <section class="panel">
        <h2>Borderline Review</h2>
        <table><thead><tr><th>Token</th><th>Route</th><th>Bucket</th><th>Outcome</th></tr></thead><tbody>{borderline_rows}</tbody></table>
      </section>
    </div>
  </div>
</body>
</html>"""


def render_diagnostics_html(hours: int = 24) -> str:
    summary = get_diagnostics_summary(hours)
    recommendations = get_diagnostics_recommendations(hours)
    counts_by_decision = summary.get("counts_by_decision") or {}
    counts_by_stage = summary.get("counts_by_stage") or {}
    top_skip_reasons = summary.get("top_skip_reasons") or []
    sessions = summary.get("sessions") or {}
    recent_examples = summary.get("recent_examples") or []
    outcomes_by_label = summary.get("outcomes_by_label") or {}
    false_negatives = summary.get("false_negatives") or []
    false_positives = summary.get("false_positives") or []
    session_quality = summary.get("session_quality") or []
    session_signal_quality = summary.get("session_signal_quality") or []
    conversion = summary.get("conversion") or {}
    reason_quality = summary.get("reason_quality") or []
    threshold_guidance = summary.get("threshold_guidance") or []
    reason_trends = summary.get("reason_trends") or []
    session_signal_trends = summary.get("session_signal_trends") or []

    def metric_card(label: str, value: Any) -> str:
        return (
            '<div class="metric-card">'
            f'<span>{html.escape(label)}</span>'
            f"<strong>{html.escape(str(value))}</strong>"
            "</div>"
        )

    def kv_row(label: str, value: Any) -> str:
        return (
            '<div class="kv-row">'
            f'<span>{html.escape(label)}</span>'
            f'<strong>{html.escape(str(value))}</strong>'
            "</div>"
        )

    top_skip_html = "".join(
        f"<li><strong>{html.escape(str(item.get('reason') or 'unknown'))}</strong><span>{int(item.get('count') or 0)}</span></li>"
        for item in top_skip_reasons
    ) or "<li><strong>No skip reasons yet</strong><span>0</span></li>"

    session_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(name))}</td>"
        f"<td>{int(stats.get('sent') or 0)}</td>"
        f"<td>{int(stats.get('skipped') or 0)}</td>"
        f"<td>{int(stats.get('blocked') or 0)}</td>"
        "</tr>"
        for name, stats in sorted(sessions.items())
    ) or "<tr><td colspan='4'>No session data yet</td></tr>"

    outcome_cards = "".join(
        metric_card(f"Outcome: {label}", count)
        for label, count in sorted(outcomes_by_label.items(), key=lambda item: (-int(item[1] or 0), item[0]))
    ) or metric_card("Outcome: pending", 0)

    false_negative_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('token') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('decision') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('outcome_label') or 'unknown'))}</td>"
        f"<td>{html.escape(', '.join(item.get('reasons') or []) or '-')}</td>"
        "</tr>"
        for item in false_negatives[:10]
    ) or "<tr><td colspan='4'>No false negatives observed</td></tr>"

    false_positive_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('token') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('event_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('outcome_label') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('market_cap_change_pct') or '-'))}%</td>"
        "</tr>"
        for item in false_positives[:10]
    ) or "<tr><td colspan='4'>No false positives observed</td></tr>"

    session_quality_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('session_bucket') or 'unknown'))}</td>"
        f"<td>{int(item.get('total') or 0)}</td>"
        f"<td>{html.escape(str(item.get('win_rate') or 0))}%</td>"
        f"<td>{html.escape(str(item.get('fail_rate') or 0))}%</td>"
        "</tr>"
        for item in session_quality
    ) or "<tr><td colspan='4'>No outcome quality data yet</td></tr>"

    session_signal_quality_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('session_bucket') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('signal_type') or 'unknown'))}</td>"
        f"<td>{int(item.get('total') or 0)}</td>"
        f"<td>{html.escape(str(item.get('win_rate') or 0))}%</td>"
        "</tr>"
        for item in session_signal_quality[:12]
    ) or "<tr><td colspan='4'>No session x signal data yet</td></tr>"

    reason_quality_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{int(item.get('total') or 0)}</td>"
        f"<td>{html.escape(str(item.get('positive_rate') or 0))}%</td>"
        f"<td>{html.escape(str(item.get('fail_rate') or 0))}%</td>"
        "</tr>"
        for item in reason_quality[:12]
    ) or "<tr><td colspan='4'>No blocker outcome data yet</td></tr>"

    threshold_guidance_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('action') or 'hold'))}</td>"
        f"<td>{html.escape(str(item.get('confidence') or 'low'))}</td>"
        f"<td>{int(item.get('sample_size') or 0)}</td>"
        "</tr>"
        for item in threshold_guidance[:10]
    ) or "<tr><td colspan='4'>No threshold guidance yet</td></tr>"

    reason_trend_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('previous_date') or '-'))}</td>"
        f"<td>{html.escape(str(item.get('current_date') or '-'))}</td>"
        f"<td>{html.escape(str(item.get('win_rate_delta') or 0))}</td>"
        "</tr>"
        for item in reason_trends[:10]
    ) or "<tr><td colspan='4'>No blocker trend data yet</td></tr>"

    session_signal_trend_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('session_bucket') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('signal_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('previous_date') or '-'))}</td>"
        f"<td>{html.escape(str(item.get('win_rate_delta') or 0))}</td>"
        "</tr>"
        for item in session_signal_trends[:10]
    ) or "<tr><td colspan='4'>No session x signal trend data yet</td></tr>"

    recent_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('token') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('stage') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('decision') or 'unknown'))}</td>"
        f"<td>{html.escape(', '.join(item.get('reasons') or []))}</td>"
        f"<td>{html.escape(str(item.get('attention_score')))}</td>"
        f"<td>{html.escape(str(item.get('risk_score')))}</td>"
        "</tr>"
        for item in recent_examples[:12]
    ) or "<tr><td colspan='6'>No recent examples yet</td></tr>"

    recommendation_html = "".join(
        '<div class="recommendation">'
        f"<h4>{html.escape(item['title'])}</h4>"
        f"<p>{html.escape(item['detail'])}</p>"
        "</div>"
        for item in recommendations
    )

    overview_cards = "".join(
        [
            metric_card("Lookback Hours", summary.get("lookback_hours", hours)),
            metric_card("Sent", sum(value for key, value in counts_by_decision.items() if str(key).endswith("sent"))),
            metric_card("Skipped", sum(value for key, value in counts_by_decision.items() if "skip" in str(key))),
            metric_card("Blocked", sum(value for key, value in counts_by_decision.items() if "block" in str(key))),
            metric_card("False Negatives", len(false_negatives)),
            metric_card("False Positives", len(false_positives)),
        ]
    )

    stage_cards = "".join(metric_card(stage, count) for stage, count in sorted(counts_by_stage.items()))
    decision_rows = "".join(kv_row(decision, count) for decision, count in sorted(counts_by_decision.items()))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Signal Diagnostics</title>
  <style>
    :root {{
      --bg: #071018;
      --panel: rgba(11, 24, 38, 0.88);
      --panel-2: rgba(18, 34, 52, 0.94);
      --line: rgba(116, 153, 186, 0.16);
      --text: #ecf4fb;
      --muted: #8ca4b8;
      --accent: #f4c430;
      --good: #2ecc71;
      --warn: #f4c430;
      --bad: #e35d6a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(244,196,48,.12), transparent 24%),
        radial-gradient(circle at bottom right, rgba(47,107,255,.14), transparent 28%),
        linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 24px 18px 36px; }}
    .hero, .grid, .wide-grid {{ display:grid; gap:16px; }}
    .hero {{ grid-template-columns: 1.2fr .8fr; margin-bottom: 18px; }}
    .grid {{ grid-template-columns: repeat(4, minmax(0,1fr)); }}
    .wide-grid {{ grid-template-columns: 1fr 1fr; margin-top: 18px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      box-shadow: 0 18px 50px rgba(0,0,0,.35);
      backdrop-filter: blur(10px);
    }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 14px; font-size: 15px; letter-spacing: .12em; color: var(--muted); text-transform: uppercase; }}
    h4 {{ margin: 0 0 8px; font-size: 15px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    .metric-row {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }}
    .metric-card, .recommendation {{
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px 16px;
    }}
    .metric-card span, .kv-row span {{ display:block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .12em; margin-bottom: 6px; }}
    .metric-card strong {{ font-size: 24px; }}
    .kv-list {{ display:grid; gap: 10px; }}
    .kv-row {{ display:flex; justify-content:space-between; gap:16px; padding:12px 14px; background: var(--panel-2); border: 1px solid var(--line); border-radius: 14px; }}
    ul.reason-list {{ list-style:none; padding:0; margin:0; display:grid; gap:10px; }}
    ul.reason-list li {{ display:flex; justify-content:space-between; gap:16px; padding:12px 14px; background: var(--panel-2); border: 1px solid var(--line); border-radius: 14px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    .recommendations {{ display:grid; gap: 12px; }}
    @media (max-width: 1020px) {{
      .hero, .wide-grid, .grid, .metric-row {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel hero">
      <div>
        <h1>Signal Diagnostics</h1>
        <p>Operator view for alert flow, skip reasons, promotion blockers, and recent decision quality over the last {int(summary.get('lookback_hours', hours))} hours.</p>
      </div>
      <div class="metric-row">{overview_cards}</div>
    </section>
    <div class="grid">
      <section class="panel">
        <h2>Stage Mix</h2>
        <div class="metric-row">{stage_cards or metric_card("No Data", 0)}</div>
      </section>
      <section class="panel">
        <h2>Decision Counts</h2>
        <div class="kv-list">{decision_rows or kv_row("No decisions", 0)}</div>
      </section>
      <section class="panel">
        <h2>Top Skip Reasons</h2>
        <ul class="reason-list">{top_skip_html}</ul>
      </section>
      <section class="panel">
        <h2>Recommendations</h2>
        <div class="recommendations">{recommendation_html}</div>
      </section>
    </div>
    <div class="grid" style="margin-top: 18px;">
      <section class="panel">
        <h2>Outcome Labels</h2>
        <div class="metric-row">{outcome_cards}</div>
      </section>
      <section class="panel">
        <h2>Candidate Conversion</h2>
        <div class="kv-list">
          {kv_row("Candidate Tokens", conversion.get("candidate_tokens", 0))}
          {kv_row("Promoted Tokens", conversion.get("promoted_tokens", 0))}
          {kv_row("Candidate -> Promoted", conversion.get("candidate_to_promoted_tokens", 0))}
        </div>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Session Breakdown</h2>
        <table>
          <thead>
            <tr><th>Session</th><th>Sent</th><th>Skipped</th><th>Blocked</th></tr>
          </thead>
          <tbody>{session_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Recent Decision Examples</h2>
        <table>
          <thead>
            <tr><th>Token</th><th>Stage</th><th>Decision</th><th>Reasons</th><th>Attn</th><th>Risk</th></tr>
          </thead>
          <tbody>{recent_rows}</tbody>
        </table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Session Outcome Quality</h2>
        <table>
          <thead>
            <tr><th>Session</th><th>Total</th><th>Positive Rate</th><th>Fail Rate</th></tr>
          </thead>
          <tbody>{session_quality_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Session x Signal Quality</h2>
        <table>
          <thead>
            <tr><th>Session</th><th>Signal</th><th>Total</th><th>Positive Rate</th></tr>
          </thead>
          <tbody>{session_signal_quality_rows}</tbody>
        </table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>False Negatives</h2>
        <table>
          <thead>
            <tr><th>Token</th><th>Decision</th><th>Outcome</th><th>Reasons</th></tr>
          </thead>
          <tbody>{false_negative_rows}</tbody>
        </table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Blocker Outcome Scorecards</h2>
        <table>
          <thead>
            <tr><th>Reason</th><th>Total</th><th>Positive Rate</th><th>Fail Rate</th></tr>
          </thead>
          <tbody>{reason_quality_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Threshold Guidance</h2>
        <table>
          <thead>
            <tr><th>Reason</th><th>Action</th><th>Confidence</th><th>Sample</th></tr>
          </thead>
          <tbody>{threshold_guidance_rows}</tbody>
        </table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Blocker Trends</h2>
        <table>
          <thead>
            <tr><th>Reason</th><th>Previous</th><th>Current</th><th>Win Delta</th></tr>
          </thead>
          <tbody>{reason_trend_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Session x Signal Trends</h2>
        <table>
          <thead>
            <tr><th>Session</th><th>Signal</th><th>Previous</th><th>Win Delta</th></tr>
          </thead>
          <tbody>{session_signal_trend_rows}</tbody>
        </table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>False Positives</h2>
        <table>
          <thead>
            <tr><th>Token</th><th>Type</th><th>Outcome</th><th>MC Change</th></tr>
          </thead>
          <tbody>{false_positive_rows}</tbody>
        </table>
      </section>
    </div>
  </div>
</body>
</html>"""


def _fetch_due_jobs(limit: int = 10) -> list[dict[str, Any]]:
    now = int(time.time())
    with _connect() as c:
        rows = c.execute(
            """
            SELECT j.signal_id, j.horizon_minutes, s.token
            FROM signal_snapshot_jobs j
            JOIN signals s ON s.signal_id = j.signal_id
            WHERE j.status='pending' AND j.due_ts <= ?
            ORDER BY j.due_ts ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
    return [
        {"signal_id": row[0], "horizon_minutes": row[1], "token": row[2]}
        for row in rows
    ]


def _mark_job_running(signal_id: str, horizon_minutes: int) -> None:
    with _connect() as c:
        c.execute(
            """
            UPDATE signal_snapshot_jobs
            SET status='running', attempts=attempts+1
            WHERE signal_id=? AND horizon_minutes=?
            """,
            (signal_id, horizon_minutes),
        )


def _mark_job_done(signal_id: str, horizon_minutes: int) -> None:
    with _connect() as c:
        c.execute(
            "UPDATE signal_snapshot_jobs SET status='done', last_error=NULL WHERE signal_id=? AND horizon_minutes=?",
            (signal_id, horizon_minutes),
        )


def _mark_job_failed(signal_id: str, horizon_minutes: int, error: str) -> None:
    with _connect() as c:
        c.execute(
            """
            UPDATE signal_snapshot_jobs
            SET status='pending', last_error=?
            WHERE signal_id=? AND horizon_minutes=?
            """,
            (error[:300], signal_id, horizon_minutes),
        )


def _get_signal_baseline(signal_id: str) -> dict[str, Any] | None:
    with _connect() as c:
        row = c.execute(
            """
            SELECT token, lifecycle, market_cap_usd, liquidity_usd, volume_m5_usd, age_minutes,
                   price_change_m5, price_change_h1, txns_m5_buys, txns_m5_sells
            FROM signals
            WHERE signal_id=?
            """,
            (signal_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "token": row[0],
        "lifecycle": row[1],
        "market_cap_usd": row[2],
        "liquidity_usd": row[3],
        "volume_m5_usd": row[4],
        "age_minutes": row[5],
        "price_change_m5": row[6],
        "price_change_h1": row[7],
        "txns_m5_buys": row[8],
        "txns_m5_sells": row[9],
    }


async def capture_snapshot(signal_id: str, horizon_minutes: int, token: str) -> None:
    baseline = _get_signal_baseline(signal_id)
    if not baseline:
        raise RuntimeError(f"missing_baseline:{signal_id}")

    dex_data = await dex_enrich_token(token)
    best_pair = select_best_pair(dex_data, token) if isinstance(dex_data, dict) else None
    dex_summary = summarize_pair(best_pair) if best_pair else {}
    snapshot = {
        "lifecycle": "dex" if dex_summary else baseline.get("lifecycle") or "unknown",
        "market_cap_usd": _to_float(dex_summary.get("market_cap") or dex_summary.get("fdv")),
        "liquidity_usd": _to_float(dex_summary.get("liquidity_usd")),
        "volume_m5_usd": _to_float(dex_summary.get("volume_m5")),
        "age_minutes": _to_float(dex_summary.get("age_minutes")),
        "price_change_m5": _to_float(dex_summary.get("price_change_m5")),
        "price_change_h1": _to_float(dex_summary.get("price_change_h1")),
        "txns_m5_buys": _to_int(dex_summary.get("txns_m5_buys")),
        "txns_m5_sells": _to_int(dex_summary.get("txns_m5_sells")),
    }
    snapshot["market_cap_change_pct"] = _percent_change(snapshot["market_cap_usd"], baseline["market_cap_usd"])
    snapshot["liquidity_change_pct"] = _percent_change(snapshot["liquidity_usd"], baseline["liquidity_usd"])
    snapshot["volume_m5_change_pct"] = _percent_change(snapshot["volume_m5_usd"], baseline["volume_m5_usd"])
    snapshot["outcome_label"] = _outcome_label(snapshot, baseline)

    with _connect() as c:
        c.execute(
            """
            INSERT INTO signal_snapshots (
                signal_id, horizon_minutes, captured_ts, lifecycle, market_cap_usd, liquidity_usd,
                volume_m5_usd, age_minutes, price_change_m5, price_change_h1, txns_m5_buys,
                txns_m5_sells, market_cap_change_pct, liquidity_change_pct, volume_m5_change_pct,
                outcome_label, snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id, horizon_minutes) DO UPDATE SET
                captured_ts=excluded.captured_ts,
                lifecycle=excluded.lifecycle,
                market_cap_usd=excluded.market_cap_usd,
                liquidity_usd=excluded.liquidity_usd,
                volume_m5_usd=excluded.volume_m5_usd,
                age_minutes=excluded.age_minutes,
                price_change_m5=excluded.price_change_m5,
                price_change_h1=excluded.price_change_h1,
                txns_m5_buys=excluded.txns_m5_buys,
                txns_m5_sells=excluded.txns_m5_sells,
                market_cap_change_pct=excluded.market_cap_change_pct,
                liquidity_change_pct=excluded.liquidity_change_pct,
                volume_m5_change_pct=excluded.volume_m5_change_pct,
                outcome_label=excluded.outcome_label,
                snapshot_json=excluded.snapshot_json
            """,
            (
                signal_id,
                horizon_minutes,
                int(time.time()),
                snapshot["lifecycle"],
                snapshot["market_cap_usd"],
                snapshot["liquidity_usd"],
                snapshot["volume_m5_usd"],
                snapshot["age_minutes"],
                snapshot["price_change_m5"],
                snapshot["price_change_h1"],
                snapshot["txns_m5_buys"],
                snapshot["txns_m5_sells"],
                snapshot["market_cap_change_pct"],
                snapshot["liquidity_change_pct"],
                snapshot["volume_m5_change_pct"],
                snapshot["outcome_label"],
                json.dumps(snapshot, sort_keys=True),
            ),
        )


async def snapshot_worker() -> None:
    init()
    while True:
        jobs = _fetch_due_jobs(limit=10)
        if not jobs:
            await asyncio.sleep(SNAPSHOT_POLL_SECONDS)
            continue
        for job in jobs:
            signal_id = str(job["signal_id"])
            horizon_minutes = int(job["horizon_minutes"])
            token = str(job["token"])
            _mark_job_running(signal_id, horizon_minutes)
            try:
                await capture_snapshot(signal_id, horizon_minutes, token)
                _mark_job_done(signal_id, horizon_minutes)
                logger.info(
                    "[signal-learning] snapshot_captured signal_id=%s token=%s horizon=%sm",
                    signal_id,
                    token,
                    horizon_minutes,
                )
            except Exception as exc:
                _mark_job_failed(signal_id, horizon_minutes, str(exc))
                logger.exception(
                    "[signal-learning] snapshot_failed signal_id=%s token=%s horizon=%sm",
                    signal_id,
                    token,
                    horizon_minutes,
                )


def generate_daily_learning_report(report_date: str | None = None) -> dict[str, Any]:
    if report_date is None:
        report_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    start_dt = datetime.strptime(report_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    with _connect() as c:
        signal_rows = c.execute(
            """
            SELECT signal_id, token, event_type, confidence_score, attention_score, risk_score,
                   elite_score, session_bucket, local_daypart, is_weekend_utc, lifecycle
            FROM signals
            WHERE alert_ts >= ? AND alert_ts < ?
            ORDER BY alert_ts ASC
            """,
            (start_ts, end_ts),
        ).fetchall()
        snapshot_rows = c.execute(
            """
            SELECT signal_id, horizon_minutes, outcome_label, market_cap_change_pct, liquidity_change_pct
            FROM signal_snapshots
            WHERE signal_id IN (
                SELECT signal_id FROM signals WHERE alert_ts >= ? AND alert_ts < ?
            )
            ORDER BY horizon_minutes DESC
            """,
            (start_ts, end_ts),
        ).fetchall()
        decision_rows = c.execute(
            """
            SELECT signal_id, decision, reasons_json
            FROM signal_decisions
            WHERE created_ts >= ? AND created_ts < ?
            ORDER BY created_ts ASC
            """,
            (start_ts, end_ts),
        ).fetchall()

    latest_snapshot_by_signal: dict[str, dict[str, Any]] = {}
    for row in snapshot_rows:
        signal_id = row[0]
        if signal_id not in latest_snapshot_by_signal:
            latest_snapshot_by_signal[signal_id] = {
                "horizon_minutes": row[1],
                "outcome_label": row[2],
                "market_cap_change_pct": row[3],
                "liquidity_change_pct": row[4],
            }

    totals_by_type: dict[str, int] = {}
    outcomes_by_label: dict[str, int] = {}
    sessions: dict[str, dict[str, Any]] = {}
    session_signal_quality_map: dict[tuple[str, str], dict[str, Any]] = {}
    failing_clusters: list[dict[str, Any]] = []
    top_blocker_counts: dict[str, int] = {}
    reason_scorecards: dict[str, dict[str, Any]] = {}

    for row in signal_rows:
        signal_id, token, event_type, confidence_score, attention_score, risk_score, elite_score, session_bucket, local_daypart, is_weekend_utc, lifecycle = row
        totals_by_type[event_type] = totals_by_type.get(event_type, 0) + 1

        latest = latest_snapshot_by_signal.get(signal_id, {})
        outcome = latest.get("outcome_label", "pending")
        outcomes_by_label[outcome] = outcomes_by_label.get(outcome, 0) + 1

        session_stats = sessions.setdefault(
            session_bucket or "unknown",
            {"count": 0, "worked": 0, "failed": 0, "avg_market_cap_change_pct": 0.0, "samples": 0},
        )
        session_stats["count"] += 1
        if outcome in {"worked", "strong_continuation"}:
            session_stats["worked"] += 1
        if outcome in {"failed", "faded"}:
            session_stats["failed"] += 1
        mc_change = latest.get("market_cap_change_pct")
        if isinstance(mc_change, (int, float)):
            session_stats["avg_market_cap_change_pct"] += float(mc_change)
            session_stats["samples"] += 1

        combo_key = (session_bucket or "unknown", event_type)
        combo_stats = session_signal_quality_map.setdefault(
            combo_key,
            {
                "session_bucket": session_bucket or "unknown",
                "signal_type": event_type,
                "total": 0,
                "positive": 0,
                "negative": 0,
            },
        )
        combo_stats["total"] += 1
        if outcome in {"worked", "strong_continuation"}:
            combo_stats["positive"] += 1
        if outcome in {"failed", "faded"}:
            combo_stats["negative"] += 1

        if outcome in {"failed", "faded"}:
            failing_clusters.append(
                {
                    "token": token,
                    "event_type": event_type,
                    "session_bucket": session_bucket,
                    "local_daypart": local_daypart,
                    "risk_score": risk_score,
                    "attention_score": attention_score,
                    "elite_score": elite_score,
                    "market_cap_change_pct": mc_change,
                    "liquidity_change_pct": latest.get("liquidity_change_pct"),
                }
            )

    for signal_id, decision, reasons_json in decision_rows:
        try:
            parsed = json.loads(reasons_json or "[]")
            reasons = [str(item) for item in parsed] if isinstance(parsed, list) else []
        except Exception:
            reasons = []
        latest = latest_snapshot_by_signal.get(signal_id, {})
        outcome = str(latest.get("outcome_label") or "pending")
        for reason in reasons:
            top_blocker_counts[reason] = top_blocker_counts.get(reason, 0) + 1
            card = reason_scorecards.setdefault(
                reason,
                {
                    "reason": reason,
                    "total": 0,
                    "positive": 0,
                    "negative": 0,
                },
            )
            card["total"] += 1
            if outcome in {"worked", "strong_continuation"}:
                card["positive"] += 1
            if outcome in {"failed", "faded"}:
                card["negative"] += 1

    for stats in sessions.values():
        if stats["samples"] > 0:
            stats["avg_market_cap_change_pct"] = round(stats["avg_market_cap_change_pct"] / stats["samples"], 2)
        else:
            stats["avg_market_cap_change_pct"] = None
        del stats["samples"]

    session_signal_quality: list[dict[str, Any]] = []
    for item in session_signal_quality_map.values():
        total = int(item["total"] or 0)
        positive = int(item["positive"] or 0)
        negative = int(item["negative"] or 0)
        session_signal_quality.append(
            {
                **item,
                "win_rate": round((positive / total) * 100.0, 1) if total else 0.0,
                "fail_rate": round((negative / total) * 100.0, 1) if total else 0.0,
            }
        )
    session_signal_quality.sort(key=lambda item: (-item["win_rate"], -item["total"], item["session_bucket"], item["signal_type"]))

    reason_quality: list[dict[str, Any]] = []
    for item in reason_scorecards.values():
        total = int(item["total"] or 0)
        positive = int(item["positive"] or 0)
        negative = int(item["negative"] or 0)
        reason_quality.append(
            {
                **item,
                "positive_rate": round((positive / total) * 100.0, 1) if total else 0.0,
                "fail_rate": round((negative / total) * 100.0, 1) if total else 0.0,
            }
        )
    reason_quality.sort(key=lambda item: (-item["total"], item["reason"]))

    threshold_guidance: list[dict[str, Any]] = []
    for item in reason_quality:
        total = int(item.get("total") or 0)
        positive_rate = float(item.get("positive_rate") or 0.0)
        fail_rate = float(item.get("fail_rate") or 0.0)
        action = "hold"
        if total >= 5 and positive_rate >= 60.0 and positive_rate >= fail_rate + 20.0:
            action = "relax_slightly"
        elif total >= 5 and fail_rate >= 60.0 and fail_rate >= positive_rate + 20.0:
            action = "tighten"
        elif total >= 3 and positive_rate >= 35.0 and fail_rate >= 35.0:
            action = "review"
        threshold_guidance.append(
            {
                "reason": item["reason"],
                "action": action,
                "sample_size": total,
                "positive_rate": positive_rate,
                "fail_rate": fail_rate,
            }
        )
    threshold_guidance.sort(
        key=lambda item: (
            {"tighten": 0, "relax_slightly": 1, "review": 2, "hold": 3}.get(str(item["action"]), 4),
            -int(item["sample_size"]),
            str(item["reason"]),
        )
    )

    tuning_snapshot = {
        "top_blockers": [
            {"reason": reason, "count": count}
            for reason, count in sorted(top_blocker_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        "top_relax_calls": [item for item in threshold_guidance if item["action"] == "relax_slightly"][:5],
        "top_tighten_calls": [item for item in threshold_guidance if item["action"] == "tighten"][:5],
        "top_review_calls": [item for item in threshold_guidance if item["action"] == "review"][:5],
        "best_session_signal": session_signal_quality[:5],
        "worst_session_signal": sorted(
            session_signal_quality,
            key=lambda item: (item["win_rate"], -item["total"], item["session_bucket"], item["signal_type"]),
        )[:5],
    }

    report = {
        "report_date": report_date,
        "generated_ts": int(time.time()),
        "totals_by_type": totals_by_type,
        "outcomes_by_label": outcomes_by_label,
        "sessions": sessions,
        "failing_clusters": failing_clusters[:20],
        "tuning_snapshot": tuning_snapshot,
    }

    with _connect() as c:
        c.execute(
            """
            INSERT INTO learning_reports (report_date, generated_ts, report_json)
            VALUES (?, ?, ?)
            ON CONFLICT(report_date) DO UPDATE SET
                generated_ts=excluded.generated_ts,
                report_json=excluded.report_json
            """,
            (report_date, report["generated_ts"], json.dumps(report, sort_keys=True)),
        )
    return report


def get_learning_report(report_date: str) -> dict[str, Any] | None:
    with _connect() as c:
        row = c.execute(
            "SELECT report_json FROM learning_reports WHERE report_date=?",
            (report_date,),
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def get_latest_learning_report() -> dict[str, Any] | None:
    with _connect() as c:
        row = c.execute(
            "SELECT report_json FROM learning_reports ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def render_learning_report_html(report_date: str | None = None) -> str:
    report = get_learning_report(report_date) if report_date else get_latest_learning_report()
    if report is None:
        raise KeyError("learning_report_not_found")

    tuning_snapshot = report.get("tuning_snapshot") if isinstance(report.get("tuning_snapshot"), dict) else {}
    totals_by_type = report.get("totals_by_type") if isinstance(report.get("totals_by_type"), dict) else {}
    outcomes_by_label = report.get("outcomes_by_label") if isinstance(report.get("outcomes_by_label"), dict) else {}
    sessions = report.get("sessions") if isinstance(report.get("sessions"), dict) else {}
    top_blockers = tuning_snapshot.get("top_blockers") if isinstance(tuning_snapshot.get("top_blockers"), list) else []
    top_relax_calls = tuning_snapshot.get("top_relax_calls") if isinstance(tuning_snapshot.get("top_relax_calls"), list) else []
    top_tighten_calls = tuning_snapshot.get("top_tighten_calls") if isinstance(tuning_snapshot.get("top_tighten_calls"), list) else []
    top_review_calls = tuning_snapshot.get("top_review_calls") if isinstance(tuning_snapshot.get("top_review_calls"), list) else []
    best_session_signal = tuning_snapshot.get("best_session_signal") if isinstance(tuning_snapshot.get("best_session_signal"), list) else []
    worst_session_signal = tuning_snapshot.get("worst_session_signal") if isinstance(tuning_snapshot.get("worst_session_signal"), list) else []
    failing_clusters = report.get("failing_clusters") if isinstance(report.get("failing_clusters"), list) else []

    def metric_card(label: str, value: Any) -> str:
        return (
            '<div class="metric-card">'
            f'<span>{html.escape(label)}</span>'
            f"<strong>{html.escape(str(value))}</strong>"
            "</div>"
        )

    totals_cards = "".join(
        metric_card(signal_type, count)
        for signal_type, count in sorted(totals_by_type.items())
    ) or metric_card("No Signals", 0)

    outcome_cards = "".join(
        metric_card(label, count)
        for label, count in sorted(outcomes_by_label.items(), key=lambda item: (-int(item[1] or 0), item[0]))
    ) or metric_card("pending", 0)

    blocker_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{int(item.get('count') or 0)}</td>"
        "</tr>"
        for item in top_blockers[:10]
    ) or "<tr><td colspan='2'>No blocker data</td></tr>"

    threshold_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('action') or 'hold'))}</td>"
        f"<td>{int(item.get('sample_size') or 0)}</td>"
        "</tr>"
        for item in (top_relax_calls[:3] + top_tighten_calls[:3] + top_review_calls[:3])
    ) or "<tr><td colspan='3'>No threshold actions</td></tr>"

    best_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('session_bucket') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('signal_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('win_rate') or 0))}%</td>"
        "</tr>"
        for item in best_session_signal[:5]
    ) or "<tr><td colspan='3'>No winning regime data</td></tr>"

    worst_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('session_bucket') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('signal_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('win_rate') or 0))}%</td>"
        "</tr>"
        for item in worst_session_signal[:5]
    ) or "<tr><td colspan='3'>No weak regime data</td></tr>"

    session_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(name))}</td>"
        f"<td>{int(stats.get('count') or 0)}</td>"
        f"<td>{int(stats.get('worked') or 0)}</td>"
        f"<td>{int(stats.get('failed') or 0)}</td>"
        f"<td>{html.escape(str(stats.get('avg_market_cap_change_pct')))}</td>"
        "</tr>"
        for name, stats in sorted(sessions.items())
    ) or "<tr><td colspan='5'>No session data</td></tr>"

    failing_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('token') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('event_type') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('session_bucket') or 'unknown'))}</td>"
        f"<td>{html.escape(str(item.get('market_cap_change_pct') or '-'))}</td>"
        "</tr>"
        for item in failing_clusters[:10]
    ) or "<tr><td colspan='4'>No failing clusters</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Learning Report {html.escape(str(report.get('report_date') or 'latest'))}</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --panel-2: rgba(18, 34, 52, 0.96);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(244,196,48,.10), transparent 25%),
        radial-gradient(circle at bottom right, rgba(47,107,255,.12), transparent 28%),
        linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1360px; margin: 0 auto; padding: 24px 18px 36px; }}
    .hero, .grid, .wide-grid {{ display:grid; gap:16px; }}
    .hero {{ grid-template-columns: 1fr; margin-bottom: 18px; }}
    .grid {{ grid-template-columns: repeat(3, minmax(0,1fr)); }}
    .wide-grid {{ grid-template-columns: 1fr 1fr; margin-top: 18px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      box-shadow: 0 18px 50px rgba(0,0,0,.35);
      backdrop-filter: blur(10px);
    }}
    h1 {{ margin: 0 0 8px; font-size: 34px; }}
    h2 {{ margin: 0 0 14px; font-size: 15px; letter-spacing: .12em; color: var(--muted); text-transform: uppercase; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    .metric-row {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }}
    .metric-card {{
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px 16px;
    }}
    .metric-card span {{ display:block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .12em; margin-bottom: 6px; }}
    .metric-card strong {{ font-size: 24px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    @media (max-width: 1020px) {{
      .grid, .wide-grid, .metric-row {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel hero">
      <div>
        <h1>Daily Learning Report</h1>
        <p>Stored tuning snapshot for {html.escape(str(report.get("report_date") or "latest"))}. This view is report-backed, not recomputed from a live dashboard window.</p>
      </div>
    </section>
    <div class="grid">
      <section class="panel">
        <h2>Totals By Type</h2>
        <div class="metric-row">{totals_cards}</div>
      </section>
      <section class="panel">
        <h2>Outcome Labels</h2>
        <div class="metric-row">{outcome_cards}</div>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Top Blockers</h2>
        <table>
          <thead><tr><th>Reason</th><th>Count</th></tr></thead>
          <tbody>{blocker_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Threshold Calls</h2>
        <table>
          <thead><tr><th>Reason</th><th>Action</th><th>Sample</th></tr></thead>
          <tbody>{threshold_rows}</tbody>
        </table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Best Session x Signal</h2>
        <table>
          <thead><tr><th>Session</th><th>Signal</th><th>Win Rate</th></tr></thead>
          <tbody>{best_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Worst Session x Signal</h2>
        <table>
          <thead><tr><th>Session</th><th>Signal</th><th>Win Rate</th></tr></thead>
          <tbody>{worst_rows}</tbody>
        </table>
      </section>
    </div>
    <div class="wide-grid">
      <section class="panel">
        <h2>Session Overview</h2>
        <table>
          <thead><tr><th>Session</th><th>Count</th><th>Worked</th><th>Failed</th><th>Avg MC %</th></tr></thead>
          <tbody>{session_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Failing Clusters</h2>
        <table>
          <thead><tr><th>Token</th><th>Type</th><th>Session</th><th>MC %</th></tr></thead>
          <tbody>{failing_rows}</tbody>
        </table>
      </section>
    </div>
  </div>
</body>
</html>"""


def get_learning_digest(report_date: str | None = None) -> dict[str, Any] | None:
    report = get_learning_report(report_date) if report_date else get_latest_learning_report()
    if report is None:
        return None

    tuning_snapshot = report.get("tuning_snapshot") if isinstance(report.get("tuning_snapshot"), dict) else {}
    top_blockers = tuning_snapshot.get("top_blockers") if isinstance(tuning_snapshot.get("top_blockers"), list) else []
    top_relax_calls = tuning_snapshot.get("top_relax_calls") if isinstance(tuning_snapshot.get("top_relax_calls"), list) else []
    top_tighten_calls = tuning_snapshot.get("top_tighten_calls") if isinstance(tuning_snapshot.get("top_tighten_calls"), list) else []
    top_review_calls = tuning_snapshot.get("top_review_calls") if isinstance(tuning_snapshot.get("top_review_calls"), list) else []
    best_session_signal = tuning_snapshot.get("best_session_signal") if isinstance(tuning_snapshot.get("best_session_signal"), list) else []
    worst_session_signal = tuning_snapshot.get("worst_session_signal") if isinstance(tuning_snapshot.get("worst_session_signal"), list) else []
    outcomes_by_label = report.get("outcomes_by_label") if isinstance(report.get("outcomes_by_label"), dict) else {}

    highlights: list[dict[str, Any]] = []
    if top_relax_calls:
        item = top_relax_calls[0]
        highlights.append(
            {
                "kind": "relax",
                "title": f"Relax {item.get('reason')}",
                "detail": f"Positive rate {item.get('positive_rate')}% across {int(item.get('sample_size') or 0)} samples.",
            }
        )
    if top_tighten_calls:
        item = top_tighten_calls[0]
        highlights.append(
            {
                "kind": "tighten",
                "title": f"Tighten {item.get('reason')}",
                "detail": f"Fail rate {item.get('fail_rate')}% across {int(item.get('sample_size') or 0)} samples.",
            }
        )
    if best_session_signal:
        item = best_session_signal[0]
        highlights.append(
            {
                "kind": "best_regime",
                "title": f"Best regime: {item.get('signal_type')} / {item.get('session_bucket')}",
                "detail": f"Win rate {item.get('win_rate')}% across {int(item.get('total') or 0)} samples.",
            }
        )
    if worst_session_signal:
        item = worst_session_signal[0]
        highlights.append(
            {
                "kind": "worst_regime",
                "title": f"Weak regime: {item.get('signal_type')} / {item.get('session_bucket')}",
                "detail": f"Win rate {item.get('win_rate')}% across {int(item.get('total') or 0)} samples.",
            }
        )
    if top_blockers:
        item = top_blockers[0]
        highlights.append(
            {
                "kind": "top_blocker",
                "title": f"Top blocker: {item.get('reason')}",
                "detail": f"Triggered {int(item.get('count') or 0)} times in the report window.",
            }
        )

    return {
        "report_date": report.get("report_date"),
        "generated_ts": report.get("generated_ts"),
        "outcomes_by_label": outcomes_by_label,
        "highlights": highlights[:5],
        "top_relax_calls": top_relax_calls[:3],
        "top_tighten_calls": top_tighten_calls[:3],
        "top_review_calls": top_review_calls[:3],
        "best_session_signal": best_session_signal[:3],
        "worst_session_signal": worst_session_signal[:3],
        "top_blockers": top_blockers[:5],
    }


def render_learning_digest_html(report_date: str | None = None) -> str:
    digest = get_learning_digest(report_date)
    if digest is None:
        raise KeyError("learning_report_not_found")

    highlights = digest.get("highlights") if isinstance(digest.get("highlights"), list) else []
    top_relax_calls = digest.get("top_relax_calls") if isinstance(digest.get("top_relax_calls"), list) else []
    top_tighten_calls = digest.get("top_tighten_calls") if isinstance(digest.get("top_tighten_calls"), list) else []
    best_session_signal = digest.get("best_session_signal") if isinstance(digest.get("best_session_signal"), list) else []
    worst_session_signal = digest.get("worst_session_signal") if isinstance(digest.get("worst_session_signal"), list) else []

    highlight_cards = "".join(
        '<div class="card">'
        f"<h3>{html.escape(str(item.get('title') or 'Update'))}</h3>"
        f"<p>{html.escape(str(item.get('detail') or ''))}</p>"
        "</div>"
        for item in highlights
    ) or '<div class="card"><h3>No highlights</h3><p>No digest highlights are available for this report.</p></div>'

    def _rows(items: list[dict[str, Any]], kind: str) -> str:
        if kind == "threshold":
            return "".join(
                "<tr>"
                f"<td>{html.escape(str(item.get('reason') or 'unknown'))}</td>"
                f"<td>{html.escape(str(item.get('action') or 'hold'))}</td>"
                f"<td>{int(item.get('sample_size') or 0)}</td>"
                "</tr>"
                for item in items
            ) or "<tr><td colspan='3'>None</td></tr>"
        return "".join(
            "<tr>"
            f"<td>{html.escape(str(item.get('session_bucket') or 'unknown'))}</td>"
            f"<td>{html.escape(str(item.get('signal_type') or 'unknown'))}</td>"
            f"<td>{html.escape(str(item.get('win_rate') or 0))}%</td>"
            "</tr>"
            for item in items
        ) or "<tr><td colspan='3'>None</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Learning Digest {html.escape(str(digest.get('report_date') or 'latest'))}</title>
  <style>
    :root {{
      --bg: #081119;
      --panel: rgba(11, 24, 38, 0.9);
      --panel-2: rgba(18, 34, 52, 0.96);
      --line: rgba(116, 153, 186, 0.16);
      --text: #edf5fb;
      --muted: #8ca4b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #071018 0%, #09131c 100%);
    }}
    .shell {{ max-width: 1200px; margin: 0 auto; padding: 24px 18px 36px; }}
    .panel, .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
    }}
    .cards, .grid {{ display:grid; gap:16px; }}
    .cards {{ grid-template-columns: repeat(2, minmax(0,1fr)); margin-top: 18px; }}
    .grid {{ grid-template-columns: 1fr 1fr; margin-top: 18px; }}
    h1 {{ margin: 0 0 8px; font-size: 32px; }}
    h2 {{ margin: 0 0 14px; font-size: 14px; letter-spacing: .12em; color: var(--muted); text-transform: uppercase; }}
    h3 {{ margin: 0 0 8px; font-size: 18px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding: 12px 10px; border-bottom: 1px solid var(--line); font-size: 14px; }}
    th {{ color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: .10em; }}
    @media (max-width: 1020px) {{
      .cards, .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>Learning Digest</h1>
      <p>Fast daily operator view for {html.escape(str(digest.get("report_date") or "latest"))}. This is the shortest path to the most actionable tuning signals.</p>
    </section>
    <section class="cards">{highlight_cards}</section>
    <div class="grid">
      <section class="panel">
        <h2>Threshold Actions</h2>
        <table>
          <thead><tr><th>Reason</th><th>Action</th><th>Sample</th></tr></thead>
          <tbody>{_rows(top_relax_calls + top_tighten_calls, "threshold")}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Review Queue</h2>
        <table>
          <thead><tr><th>Reason</th><th>Action</th><th>Sample</th></tr></thead>
          <tbody>{_rows(digest.get("top_review_calls") or [], "threshold")}</tbody>
        </table>
      </section>
    </div>
    <div class="grid">
      <section class="panel">
        <h2>Best Regimes</h2>
        <table>
          <thead><tr><th>Session</th><th>Signal</th><th>Win Rate</th></tr></thead>
          <tbody>{_rows(best_session_signal, "regime")}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Weak Regimes</h2>
        <table>
          <thead><tr><th>Session</th><th>Signal</th><th>Win Rate</th></tr></thead>
          <tbody>{_rows(worst_session_signal, "regime")}</tbody>
        </table>
      </section>
    </div>
  </div>
</body>
</html>"""


async def daily_report_worker() -> None:
    init()
    while True:
        try:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            with _connect() as c:
                row = c.execute(
                    "SELECT report_date FROM learning_reports WHERE report_date=?",
                    (yesterday,),
                ).fetchone()
            if not row:
                report = generate_daily_learning_report(yesterday)
                logger.info(
                    "[signal-learning] daily_report_generated date=%s totals=%s outcomes=%s",
                    yesterday,
                    report.get("totals_by_type"),
                    report.get("outcomes_by_label"),
                )
        except Exception:
            logger.exception("[signal-learning] daily_report_failed")
        await asyncio.sleep(REPORT_POLL_SECONDS)

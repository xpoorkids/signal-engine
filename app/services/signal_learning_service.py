from __future__ import annotations

import asyncio
import html
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from worker.dex import dex_enrich_token, select_best_pair, summarize_pair

try:
    from zoneinfo import ZoneInfo

    CHICAGO_TZ = ZoneInfo("America/Chicago")
except Exception:
    CHICAGO_TZ = timezone.utc


logger = logging.getLogger(__name__)

DB_PATH = Path("state/engine.db")
SNAPSHOT_HORIZONS_MINUTES = (5, 15, 60, 240)
SNAPSHOT_POLL_SECONDS = 30
REPORT_POLL_SECONDS = 600


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
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
            CREATE TABLE IF NOT EXISTS signal_decisions (
                decision_id TEXT PRIMARY KEY,
                token TEXT,
                event_type TEXT,
                stage TEXT,
                decision TEXT NOT NULL,
                reasons_json TEXT,
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
        c.execute("CREATE INDEX IF NOT EXISTS idx_signals_alert_ts ON signals(alert_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_signal_jobs_due ON signal_snapshot_jobs(status, due_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_signal ON signal_snapshots(signal_id, horizon_minutes)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ts ON signal_decisions(created_ts, decision)")
        decision_cols = {row[1] for row in c.execute("PRAGMA table_info(signal_decisions)").fetchall()}
        if "signal_id" not in decision_cols:
            c.execute("ALTER TABLE signal_decisions ADD COLUMN signal_id TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_decisions_signal_id ON signal_decisions(signal_id)")


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


def record_signal_event(event, *, external_ref: str | None = None, edited: bool = False) -> str:
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


def record_signal_decision(
    *,
    token: str | None,
    event_type: str,
    stage: str,
    decision: str,
    reasons: list[str] | None = None,
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
                decision_id, signal_id, token, event_type, stage, decision, reasons_json,
                attention_score, risk_score, confidence_score, creator_score, lifecycle,
                hour_utc, day_of_week_utc, is_weekend_utc, hour_local, day_of_week_local,
                local_daypart, session_bucket, created_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                resolved_signal_id,
                token,
                event_type,
                stage,
                decision,
                json.dumps(reasons or []),
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


def get_diagnostics_summary(hours: int = 24) -> dict[str, Any]:
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
        recs.append(
            {
                "title": "Weakest Session x Signal",
                "detail": (
                    f"`{worst_combo['signal_type']}` in `{worst_combo['session_bucket']}` is weakest at "
                    f"{worst_combo['win_rate']}% positive outcomes across {worst_combo['total']} samples."
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

from __future__ import annotations

import asyncio
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
        c.execute("CREATE INDEX IF NOT EXISTS idx_signals_alert_ts ON signals(alert_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_signal_jobs_due ON signal_snapshot_jobs(status, due_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_signal ON signal_snapshots(signal_id, horizon_minutes)")


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


def record_signal_event(event, *, external_ref: str | None = None, edited: bool = False) -> str:
    now = int(time.time())
    extra = event.extra if isinstance(event.extra, dict) else {}
    metrics = _extract_extra_metrics(extra)
    time_features = _classify_time_features(event.ts)
    payload_json = json.dumps(extra, sort_keys=True)
    existing_signal_id = None

    with _connect() as c:
        if external_ref:
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
                SET updated_ts=?, confidence_score=?, attention_score=?, risk_score=?, elite_score=?,
                    market_cap_usd=?, liquidity_usd=?, volume_m5_usd=?, age_minutes=?, price_change_m5=?,
                    price_change_h1=?, txns_m5_buys=?, txns_m5_sells=?, lifecycle=?, payload_json=?
                WHERE signal_id=?
                """,
                (
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

        for horizon in SNAPSHOT_HORIZONS_MINUTES:
            c.execute(
                """
                INSERT INTO signal_snapshot_jobs (signal_id, horizon_minutes, due_ts, status)
                VALUES (?, ?, ?, 'pending')
                ON CONFLICT(signal_id, horizon_minutes) DO NOTHING
                """,
                (signal_id, horizon, int(event.ts) + (horizon * 60)),
            )

    logger.info(
        "[signal-learning] signal_recorded signal_id=%s token=%s type=%s edited=%s external_ref=%s",
        signal_id,
        event.token,
        event.type,
        edited,
        external_ref or "",
    )
    return signal_id


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
    failing_clusters: list[dict[str, Any]] = []

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

    for stats in sessions.values():
        if stats["samples"] > 0:
            stats["avg_market_cap_change_pct"] = round(stats["avg_market_cap_change_pct"] / stats["samples"], 2)
        else:
            stats["avg_market_cap_change_pct"] = None
        del stats["samples"]

    report = {
        "report_date": report_date,
        "generated_ts": int(time.time()),
        "totals_by_type": totals_by_type,
        "outcomes_by_label": outcomes_by_label,
        "sessions": sessions,
        "failing_clusters": failing_clusters[:20],
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

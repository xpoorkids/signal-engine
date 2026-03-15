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
) -> str:
    created_ts = int(ts_value or time.time())
    time_features = _classify_time_features(created_ts)
    decision_id = uuid.uuid4().hex
    with _connect() as c:
        c.execute(
            """
            INSERT INTO signal_decisions (
                decision_id, token, event_type, stage, decision, reasons_json,
                attention_score, risk_score, confidence_score, creator_score, lifecycle,
                hour_utc, day_of_week_utc, is_weekend_utc, hour_local, day_of_week_local,
                local_daypart, session_bucket, created_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
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
    return decision_id


def get_diagnostics_summary(hours: int = 24) -> dict[str, Any]:
    cutoff = int(time.time()) - max(1, hours) * 3600
    with _connect() as c:
        decision_rows = c.execute(
            """
            SELECT decision, reasons_json, session_bucket, local_daypart,
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
    outcome_counts: dict[str, int] = {}
    false_positives: list[dict[str, Any]] = []
    session_outcomes: dict[str, dict[str, Any]] = {}
    conversion_by_token: dict[str, set[str]] = {}

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
            "horizon_minutes": horizon_minutes,
            "outcome_label": outcome_label,
            "market_cap_change_pct": market_cap_change_pct,
            "liquidity_change_pct": liquidity_change_pct,
        }
        outcome_by_token.setdefault(token_key, []).append(outcome_entry)
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
        decision, reasons_json, session_bucket, local_daypart, attention_score, risk_score, confidence_score, created_ts, token, stage = row
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
        decision, reasons_json, session_bucket, local_daypart, attention_score, risk_score, confidence_score, created_ts, token, stage = row
        if decision.endswith("sent"):
            continue
        token_outcomes = outcome_by_token.get(token or "")
        if not token_outcomes:
            continue
        best_outcome = None
        for outcome_entry in token_outcomes:
            if outcome_entry["outcome_label"] in {"worked", "strong_continuation"}:
                best_outcome = outcome_entry
                break
        if not best_outcome:
            continue
        reasons: list[str] = []
        try:
            parsed = json.loads(reasons_json or "[]")
            if isinstance(parsed, list):
                reasons = [str(item) for item in parsed]
        except Exception:
            reasons = []
        false_negatives.append(
            {
                "token": token,
                "stage": stage,
                "decision": decision,
                "reasons": reasons,
                "outcome_label": best_outcome["outcome_label"],
                "session_bucket": best_outcome["session_bucket"],
                "market_cap_change_pct": best_outcome["market_cap_change_pct"],
                "horizon_minutes": best_outcome["horizon_minutes"],
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
        "conversion": conversion,
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
    conversion = summary.get("conversion") or {}

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
    conversion = summary.get("conversion") or {}

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

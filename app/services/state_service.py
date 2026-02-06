import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("state/engine.db")
DB_PATH.parent.mkdir(exist_ok=True)


def _connect():
    return sqlite3.connect(DB_PATH)


def init():
    with _connect() as c:
        c.execute(
            """
        CREATE TABLE IF NOT EXISTS token_state (
            token TEXT PRIMARY KEY,
            last_sent INTEGER,
            sent_count INTEGER DEFAULT 0,
            first_seen INTEGER,
            last_seen INTEGER,
            last_metrics TEXT,
            muted_until INTEGER DEFAULT 0,
            confirm_count INTEGER DEFAULT 0,
            confirm_window_start INTEGER DEFAULT 0,
            last_severity TEXT DEFAULT 'near_pass'
        )
        """
        )
        c.execute(
            """
        CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY,
            v TEXT
        )
        """
        )


def upsert_seen(token: str, metrics: dict):
    now = int(time.time())
    with _connect() as c:
        row = c.execute("SELECT token FROM token_state WHERE token=?", (token,)).fetchone()
        metrics_json = json.dumps(metrics or {})
        if not row:
            c.execute(
                """
                INSERT INTO token_state (token, first_seen, last_seen, last_metrics)
                VALUES (?, ?, ?, ?)
            """,
                (token, now, now, metrics_json),
            )
        else:
            c.execute(
                """
                UPDATE token_state SET last_seen=?, last_metrics=? WHERE token=?
            """,
                (now, metrics_json, token),
            )


def should_mute(token: str) -> int:
    now = int(time.time())
    with _connect() as c:
        row = c.execute(
            "SELECT muted_until FROM token_state WHERE token=?",
            (token,),
        ).fetchone()
        if not row:
            return 0
        muted_until = row[0] or 0
        return muted_until if muted_until > now else 0


def adaptive_cooldown(base_cooldown: int, sent_count: int) -> int:
    if sent_count <= 1:
        return base_cooldown
    if sent_count <= 3:
        return int(base_cooldown * 1.5)
    return int(base_cooldown * 2.5)


def allow_alert(token: str, base_cooldown: int) -> bool:
    now = int(time.time())
    with _connect() as c:
        row = c.execute(
            """
            SELECT last_sent, sent_count, muted_until
            FROM token_state WHERE token=?
        """,
            (token,),
        ).fetchone()

        if not row:
            c.execute(
                """
                INSERT INTO token_state (token, last_sent, sent_count, first_seen, last_seen)
                VALUES (?, ?, 1, ?, ?)
            """,
                (token, now, now, now),
            )
            return True

        last_sent, sent_count, muted_until = row
        muted_until = muted_until or 0
        if muted_until > now:
            return False

        cd = adaptive_cooldown(base_cooldown, sent_count or 0)
        if not last_sent:
            c.execute(
                "UPDATE token_state SET last_sent=?, sent_count=sent_count+1 WHERE token=?",
                (now, token),
            )
            return True

        if now - last_sent >= cd:
            c.execute(
                "UPDATE token_state SET last_sent=?, sent_count=sent_count+1 WHERE token=?",
                (now, token),
            )
            return True

        return False


def maybe_auto_mute(
    token: str,
    window_minutes: int,
    after_alerts: int,
    mute_minutes: int,
) -> bool:
    now = int(time.time())
    window_sec = window_minutes * 60
    mute_sec = mute_minutes * 60

    with _connect() as c:
        row = c.execute(
            """
            SELECT sent_count, last_sent, first_seen, muted_until
            FROM token_state WHERE token=?
        """,
            (token,),
        ).fetchone()
        if not row:
            return False

        sent_count, last_sent, first_seen, muted_until = row
        if (muted_until or 0) > now:
            return True

        if first_seen and (now - first_seen) <= window_sec and (sent_count or 0) >= after_alerts:
            c.execute(
                "UPDATE token_state SET muted_until=? WHERE token=?",
                (now + mute_sec, token),
            )
            return True

        return False


def update_severity(token: str, severity: str):
    with _connect() as c:
        c.execute("UPDATE token_state SET last_severity=? WHERE token=?", (severity, token))


def pass_escalation_check(
    token: str,
    metrics: dict,
    pass_confirmations: int,
    pass_window_minutes: int,
    min_liq: float,
    min_vol5m: float,
) -> bool:
    now = int(time.time())
    window_sec = pass_window_minutes * 60

    liq = float(metrics.get("liquidity", 0) or 0)
    vol5m = float(metrics.get("volume_5m", 0) or 0)

    if liq < min_liq or vol5m < min_vol5m:
        return False

    with _connect() as c:
        row = c.execute(
            """
            SELECT confirm_count, confirm_window_start
            FROM token_state WHERE token=?
        """,
            (token,),
        ).fetchone()

        if not row:
            return False

        confirm_count, start = row
        confirm_count = confirm_count or 0
        start = start or 0

        if start == 0 or (now - start) > window_sec:
            confirm_count = 1
            start = now
        else:
            confirm_count += 1

        c.execute(
            """
            UPDATE token_state SET confirm_count=?, confirm_window_start=?
            WHERE token=?
        """,
            (confirm_count, start, token),
        )

        return confirm_count >= pass_confirmations


def kv_get(key: str, default: str = "") -> str:
    with _connect() as c:
        row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row[0] if row and row[0] is not None else default


def kv_set(key: str, value: str):
    with _connect() as c:
        c.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )


def top_recent(limit: int = 25, lookback_hours: int = 24):
    now = int(time.time())
    cutoff = now - lookback_hours * 3600
    with _connect() as c:
        rows = c.execute(
            """
            SELECT token, last_seen, last_metrics, last_severity, sent_count
            FROM token_state
            WHERE last_seen >= ?
            ORDER BY last_seen DESC
            LIMIT ?
        """,
            (cutoff, limit),
        ).fetchall()

    out = []
    for token, last_seen, last_metrics, last_severity, sent_count in rows:
        try:
            metrics = json.loads(last_metrics) if last_metrics else {}
        except Exception:
            metrics = {}
        out.append(
            {
                "token": token,
                "last_seen": last_seen,
                "metrics": metrics,
                "severity": last_severity,
                "sent_count": sent_count or 0,
            }
        )
    return out


def record_alert(token: str, severity: str):
    now = int(time.time())
    with _connect() as c:
        c.execute(
            """
            INSERT INTO token_state (token, last_sent, sent_count, first_seen, last_seen, last_severity)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET
                last_sent=excluded.last_sent,
                sent_count=sent_count+1,
                last_seen=excluded.last_seen,
                last_severity=excluded.last_severity
        """,
            (token, now, now, now, severity),
        )


def record_repeat(token: str, severity: str) -> dict:
    now = int(time.time())
    with _connect() as c:
        row = c.execute(
            """
            SELECT first_seen, last_seen, sent_count
            FROM token_state WHERE token=?
        """,
            (token,),
        ).fetchone()

        if not row:
            return {}

        first_seen, last_seen, sent_count = row
        c.execute(
            """
            UPDATE token_state
            SET last_seen=?, last_severity=?
            WHERE token=?
        """,
            (now, severity, token),
        )

        return {
            "first_seen": first_seen,
            "last_seen": now,
            "repeat_count": sent_count or 1,
        }

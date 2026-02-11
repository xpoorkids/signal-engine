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
        # add candidate-specific columns (safe if already exists)
        try:
            c.execute("ALTER TABLE token_state ADD COLUMN candidate_last_sent INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE token_state ADD COLUMN candidate_sent_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        c.execute(
            """
        CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY,
            v TEXT
        )
        """
        )
        c.execute(
            """
        CREATE TABLE IF NOT EXISTS candidate_state (
            token TEXT PRIMARY KEY,
            first_seen_at INTEGER,
            last_update_at INTEGER,
            last_attention REAL,
            last_score REAL,
            last_liquidity REAL,
            last_unique_buyers INTEGER,
            creator_score REAL,
            lifecycle TEXT,
            alert_sent INTEGER DEFAULT 0,
            message_id TEXT,
            promo_confirm_count INTEGER DEFAULT 0,
            next_check_at INTEGER,
            stage TEXT,
            flat_liq_count INTEGER DEFAULT 0,
            flat_buyer_count INTEGER DEFAULT 0,
            max_confidence REAL DEFAULT 0.0
        )
        """
        )
        # add optional columns for existing DBs
        for stmt in (
            "ALTER TABLE candidate_state ADD COLUMN next_check_at INTEGER",
            "ALTER TABLE candidate_state ADD COLUMN stage TEXT",
            "ALTER TABLE candidate_state ADD COLUMN flat_liq_count INTEGER DEFAULT 0",
            "ALTER TABLE candidate_state ADD COLUMN flat_buyer_count INTEGER DEFAULT 0",
            "ALTER TABLE candidate_state ADD COLUMN max_confidence REAL DEFAULT 0.0",
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass
        c.execute(
            """
        CREATE TABLE IF NOT EXISTS creator_state (
            creator TEXT PRIMARY KEY,
            first_seen INTEGER,
            last_seen INTEGER,
            deploy_count INTEGER DEFAULT 0
        )
        """
        )
        c.execute(
            """
        CREATE TABLE IF NOT EXISTS creator_deploys (
            creator TEXT,
            ts INTEGER
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


def get_last_metrics(token: str) -> dict:
    with _connect() as c:
        row = c.execute(
            "SELECT last_metrics FROM token_state WHERE token=?",
            (token,),
        ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def record_candidate_sent(token: str) -> None:
    now = int(time.time())
    with _connect() as c:
        row = c.execute(
            "SELECT token FROM token_state WHERE token=?",
            (token,),
        ).fetchone()
        if not row:
            c.execute(
                """
                INSERT INTO token_state (token, candidate_last_sent, candidate_sent_count, first_seen, last_seen)
                VALUES (?, ?, 1, ?, ?)
            """,
                (token, now, now, now),
            )
            return
        c.execute(
            """
            UPDATE token_state
            SET candidate_last_sent=?, candidate_sent_count=candidate_sent_count+1
            WHERE token=?
        """,
            (now, token),
        )


def get_candidate_state(token: str) -> dict:
    with _connect() as c:
        row = c.execute(
            """
            SELECT candidate_last_sent, candidate_sent_count, last_metrics
            FROM token_state WHERE token=?
        """,
            (token,),
        ).fetchone()
    if not row:
        return {"candidate_last_sent": 0, "candidate_sent_count": 0, "last_metrics": {}}
    last_metrics = {}
    try:
        last_metrics = json.loads(row[2]) if row[2] else {}
    except Exception:
        last_metrics = {}
    return {
        "candidate_last_sent": row[0] or 0,
        "candidate_sent_count": row[1] or 0,
        "last_metrics": last_metrics,
    }


def get_candidate_state(token: str) -> dict:
    with _connect() as c:
        row = c.execute(
            """
            SELECT first_seen_at, last_update_at, last_attention, last_score,
                   last_liquidity, last_unique_buyers, creator_score, lifecycle,
                   alert_sent, message_id, promo_confirm_count, next_check_at,
                   stage, flat_liq_count, flat_buyer_count, max_confidence
            FROM candidate_state WHERE token=?
        """,
            (token,),
        ).fetchone()
    if not row:
        return {}
    return {
        "first_seen_at": row[0] or 0,
        "last_update_at": row[1] or 0,
        "last_attention": row[2] or 0.0,
        "last_score": row[3] or 0.0,
        "last_liquidity": row[4] or 0.0,
        "last_unique_buyers": row[5] or 0,
        "creator_score": row[6] or 0.0,
        "lifecycle": row[7] or "",
        "alert_sent": row[8] or 0,
        "message_id": row[9] or "",
        "promo_confirm_count": row[10] or 0,
        "next_check_at": row[11] or 0,
        "stage": row[12] or "",
        "flat_liq_count": row[13] or 0,
        "flat_buyer_count": row[14] or 0,
        "max_confidence": row[15] or 0.0,
    }


def upsert_candidate_state(
    token: str,
    attention: float,
    score: float,
    liquidity: float,
    unique_buyers: int,
    creator_score: float,
    lifecycle: str,
) -> None:
    now = int(time.time())
    with _connect() as c:
        row = c.execute(
            "SELECT token FROM candidate_state WHERE token=?",
            (token,),
        ).fetchone()
        if not row:
            c.execute(
                """
                INSERT INTO candidate_state (
                    token, first_seen_at, last_update_at, last_attention, last_score,
                    last_liquidity, last_unique_buyers, creator_score, lifecycle, max_confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    token,
                    now,
                    now,
                    attention,
                    score,
                    liquidity,
                    unique_buyers,
                    creator_score,
                    lifecycle,
                    score,
                ),
            )
        else:
            c.execute(
                """
                UPDATE candidate_state
                SET last_update_at=?, last_attention=?, last_score=?, last_liquidity=?,
                    last_unique_buyers=?, creator_score=?, lifecycle=?, max_confidence=MAX(max_confidence, ?)
                WHERE token=?
            """,
                (
                    now,
                    attention,
                    score,
                    liquidity,
                    unique_buyers,
                    creator_score,
                    lifecycle,
                    score,
                    token,
                ),
            )


def update_candidate_recheck(
    token: str,
    next_check_at: int,
    stage: str,
) -> None:
    with _connect() as c:
        c.execute(
            """
            UPDATE candidate_state
            SET next_check_at=?, stage=?
            WHERE token=?
        """,
            (next_check_at, stage, token),
        )


def update_candidate_flat_counters(
    token: str,
    flat_liq_count: int,
    flat_buyer_count: int,
) -> None:
    with _connect() as c:
        c.execute(
            """
            UPDATE candidate_state
            SET flat_liq_count=?, flat_buyer_count=?
            WHERE token=?
        """,
            (flat_liq_count, flat_buyer_count, token),
        )


def mark_candidate_alert_sent(token: str) -> None:
    now = int(time.time())
    with _connect() as c:
        c.execute(
            """
            UPDATE candidate_state
            SET alert_sent=1, last_update_at=?
            WHERE token=?
        """,
            (now, token),
        )


def update_candidate_message_id(token: str, message_id: str) -> None:
    if not message_id:
        return
    with _connect() as c:
        c.execute(
            "UPDATE candidate_state SET message_id=? WHERE token=?",
            (message_id, token),
        )


def update_promo_confirm(token: str, passed: bool) -> int:
    with _connect() as c:
        row = c.execute(
            "SELECT promo_confirm_count FROM candidate_state WHERE token=?",
            (token,),
        ).fetchone()
        count = row[0] if row and row[0] is not None else 0
        if passed:
            count += 1
        else:
            count = 0
        c.execute(
            "UPDATE candidate_state SET promo_confirm_count=? WHERE token=?",
            (count, token),
        )
    return count


def allow_candidate_rate_limit(max_per_hour: int) -> bool:
    now = int(time.time())
    window_key = "candidate_rate_window_start"
    count_key = "candidate_rate_window_count"
    with _connect() as c:
        start_row = c.execute("SELECT v FROM kv WHERE k=?", (window_key,)).fetchone()
        count_row = c.execute("SELECT v FROM kv WHERE k=?", (count_key,)).fetchone()
        start = int(start_row[0]) if start_row and start_row[0] else 0
        count = int(count_row[0]) if count_row and count_row[0] else 0
        if start == 0 or now - start >= 3600:
            start = now
            count = 0
        if count >= max_per_hour:
            c.execute(
                "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (window_key, str(start)),
            )
            c.execute(
                "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (count_key, str(count)),
            )
            return False
        count += 1
        c.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (window_key, str(start)),
        )
        c.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (count_key, str(count)),
        )
        return True


def record_creator_deploy(creator: str) -> None:
    if not creator:
        return
    now = int(time.time())
    with _connect() as c:
        row = c.execute(
            "SELECT creator, first_seen, deploy_count FROM creator_state WHERE creator=?",
            (creator,),
        ).fetchone()
        if not row:
            c.execute(
                """
                INSERT INTO creator_state (creator, first_seen, last_seen, deploy_count)
                VALUES (?, ?, ?, 1)
            """,
                (creator, now, now),
            )
        else:
            c.execute(
                """
                UPDATE creator_state SET last_seen=?, deploy_count=deploy_count+1
                WHERE creator=?
            """,
                (now, creator),
            )
        c.execute(
            "INSERT INTO creator_deploys (creator, ts) VALUES (?, ?)",
            (creator, now),
        )


def get_creator_stats(creator: str) -> dict:
    if not creator:
        return {"deploys_24h": 0, "deploys_lifetime": 0, "first_seen": 0}
    now = int(time.time())
    cutoff = now - 24 * 3600
    with _connect() as c:
        row = c.execute(
            "SELECT first_seen, deploy_count FROM creator_state WHERE creator=?",
            (creator,),
        ).fetchone()
        deploys_lifetime = row[1] if row else 0
        first_seen = row[0] if row else 0
        row2 = c.execute(
            "SELECT COUNT(1) FROM creator_deploys WHERE creator=? AND ts>=?",
            (creator, cutoff),
        ).fetchone()
        deploys_24h = row2[0] if row2 else 0
    return {
        "deploys_24h": deploys_24h,
        "deploys_lifetime": deploys_lifetime,
        "first_seen": first_seen,
    }


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

import os
import time
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Chicago")
except Exception:
    LOCAL_TZ = timezone.utc

from app.services.scan_service import process_scan
from app.services.state_service import (
    init,
    upsert_seen,
    allow_alert,
    maybe_auto_mute,
    pass_escalation_check,
    update_severity,
    top_recent,
    kv_get,
    kv_set,
    record_alert,
    record_repeat,
)
from app.services.discord_service import send_candidate, send_text, send_collapsed_repeat
from app.services.explain_service import one_sentence_explanation
from app.services.wallet_service import wallet_risk_score

DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")
DISCORD_ENABLED = os.getenv("ENABLE_DISCORD", "true").lower() in ("1", "true", "yes")
DEX_ENABLED = os.getenv("ENABLE_DEX", "true").lower() in ("1", "true", "yes")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
BASE_COOLDOWN = int(os.getenv("BASE_COOLDOWN_SECONDS", "900"))

PASS_CONFIRMATIONS = int(os.getenv("PASS_CONFIRMATIONS", "3"))
PASS_WINDOW_MINUTES = int(os.getenv("PASS_WINDOW_MINUTES", "10"))
PASS_MIN_LIQUIDITY = float(os.getenv("PASS_MIN_LIQUIDITY", "15000"))
PASS_MIN_VOL5M = float(os.getenv("PASS_MIN_VOL5M", "8000"))

MUTE_AFTER_ALERTS = int(os.getenv("MUTE_AFTER_ALERTS", "4"))
MUTE_WINDOW_MINUTES = int(os.getenv("MUTE_WINDOW_MINUTES", "15"))
MUTE_DURATION_MINUTES = int(os.getenv("MUTE_DURATION_MINUTES", "60"))

COLLAPSE_EVERY = int(os.getenv("COLLAPSE_EVERY", "3"))
HEATING_UP_AFTER = int(os.getenv("HEATING_UP_AFTER", "5"))

DIGEST_HOUR_LOCAL = int(os.getenv("DIGEST_HOUR_LOCAL", "18"))
DIGEST_MINUTE_LOCAL = int(os.getenv("DIGEST_MINUTE_LOCAL", "0"))

WALLET_SCORE_ENABLED = os.getenv(
    "ENABLE_WALLET",
    os.getenv("WALLET_SCORE_ENABLED", "false"),
).lower() in ("1", "true", "yes")

HEARTBEAT_EVERY = 10  # cycles


def log(m: str):
    print(m, flush=True)


def should_send_collapsed_repeat(stats: dict) -> bool:
    cnt = stats.get("repeat_count", 0)
    return cnt > 1 and cnt % COLLAPSE_EVERY == 0


def is_heating_up(stats: dict) -> bool:
    return stats.get("repeat_count", 0) >= HEATING_UP_AFTER


def should_send_digest_now() -> bool:
    now_local = datetime.now(LOCAL_TZ)
    key = "digest_last_sent_yyyymmdd"
    last = kv_get(key, "")

    today = now_local.strftime("%Y%m%d")
    if last == today:
        return False

    if (now_local.hour > DIGEST_HOUR_LOCAL) or (
        now_local.hour == DIGEST_HOUR_LOCAL and now_local.minute >= DIGEST_MINUTE_LOCAL
    ):
        kv_set(key, today)
        return True
    return False


def send_daily_digest():
    items = top_recent(limit=25, lookback_hours=24)
    if not items:
        return

    lines = ["📊 **Daily Digest (last 24h)**"]
    for i in items[:15]:
        m = i["metrics"] or {}
        liq = m.get("liquidity", 0)
        vol5m = m.get("volume_5m", 0)
        age = m.get("age_minutes", 0)
        sev = i.get("severity", "near_pass")
        cnt = i.get("sent_count", 0)

        lines.append(
            f"- `{i['token'][:4]}{i['token'][-4:]}` "
            f"sev={sev} alerts={cnt} liq=${liq:,.0f} vol5m=${vol5m:,.0f} age={age:.0f}m"
        )

    msg = "\n".join(lines)
    if not DRY_RUN and DISCORD_ENABLED:
        send_text(msg, mode="digest", fanout=False)


def _process_candidate(c: dict) -> None:
    token = c["token"]
    metrics = c.get("metrics", {})
    upsert_seen(token, metrics)

    if WALLET_SCORE_ENABLED:
        risk = wallet_risk_score(token)
        if risk.get("enabled") and risk.get("risk") in ("warn", "high"):
            c["reason"] = f"rug_wallet_{risk.get('reason')}"
            mode = "rug"
            update_severity(token, "rug")
        else:
            mode = "near_pass"
        c["wallet"] = risk
    else:
        mode = "near_pass"

    muted = maybe_auto_mute(
        token,
        MUTE_WINDOW_MINUTES,
        MUTE_AFTER_ALERTS,
        MUTE_DURATION_MINUTES,
    )
    if muted:
        return

    if mode != "rug":
        if pass_escalation_check(
            token=token,
            metrics=metrics,
            pass_confirmations=PASS_CONFIRMATIONS,
            pass_window_minutes=PASS_WINDOW_MINUTES,
            min_liq=PASS_MIN_LIQUIDITY,
            min_vol5m=PASS_MIN_VOL5M,
        ):
            mode = "pass"
            c["escalated_from"] = "near_pass"
            update_severity(token, "pass")
        else:
            update_severity(token, "near_pass")

    if mode == "pass":
        explanation = one_sentence_explanation(c, mode)
        if not DRY_RUN and DISCORD_ENABLED:
            send_candidate(c, mode=mode, explanation=explanation)
        record_alert(token, mode)
        return

    if allow_alert(token, BASE_COOLDOWN):
        explanation = one_sentence_explanation(c, mode)
        if not DRY_RUN and DISCORD_ENABLED:
            send_candidate(c, mode=mode, explanation=explanation)
        record_alert(token, mode)
    else:
        stats = record_repeat(token, mode)
        if should_send_collapsed_repeat(stats):
            heating = is_heating_up(stats)
            log(
                f"[repeat] {mode} {c.get('symbol')} "
                f"count={stats.get('repeat_count')} "
                f"{'HEATING_UP' if heating else ''}"
            )
            if not DRY_RUN and DISCORD_ENABLED:
                send_collapsed_repeat(c, mode=mode, stats=stats, heating_up=heating)


def process_early_candidate(candidate: dict) -> None:
    """
    Early candidate from Helius WS.
    Uses the same downstream pipeline as polling candidates.
    """
    candidate["source"] = "helius"
    candidate["early"] = True
    _process_candidate(candidate)


def process_candidate(candidate: dict) -> None:
    _process_candidate(candidate)


def run():
    log("[worker] starting")
    init()
    cycle = 0

    while True:
        cycle += 1
        try:
            if cycle % HEARTBEAT_EVERY == 0:
                hb = (
                    f"[worker] heartbeat {datetime.now(timezone.utc).isoformat()} "
                    f"cycle={cycle} DRY_RUN={DRY_RUN}"
                )
                log(hb)
                if not DRY_RUN and DISCORD_ENABLED:
                    send_text(hb, mode="logs", fanout=False)

            if should_send_digest_now():
                log("[digest] sending daily digest")
                send_daily_digest()

            hits = []
            if DEX_ENABLED:
                hits = process_scan()
                log(f"[worker] candidates={len(hits)}")

                for c in hits:
                    _process_candidate(c)
            else:
                if cycle == 1:
                    log("[worker] dex polling disabled")

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            log(f"[worker] error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run()

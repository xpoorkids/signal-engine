from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Any

from app.services.db_service import connect_sqlite, resolve_engine_db_path
from worker.dex import dex_enrich_token, select_best_pair, summarize_pair
from worker.trade_validator import build_pair_context, simulate_sell_quote


logger = logging.getLogger(__name__)
DB_PATH = resolve_engine_db_path()
_SCHEMA_READY = False


@dataclass(frozen=True)
class ShadowPosition:
    position_id: str
    signal_id: str | None
    token: str
    source_event_type: str
    status: str
    opened_ts: int
    intended_size_usd: float
    position_size_tokens: float
    pair_address: str | None
    dex_id: str | None
    entry_mid_price_usd: float | None
    entry_exec_price_usd: float
    expected_buy_slippage_bps: float
    expected_sell_slippage_bps: float
    take_profit_pct: float
    stop_loss_pct: float
    max_hold_minutes: int
    decision_context: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _connect() -> sqlite3.Connection:
    return connect_sqlite(_current_db_path())


def _current_db_path() -> Path:
    return resolve_engine_db_path(DB_PATH)


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _poll_seconds() -> int:
    try:
        return max(5, int(os.getenv("SHADOW_EXECUTION_POLL_SECONDS", "30")))
    except Exception:
        return 30


def _take_profit_pct() -> float:
    try:
        return float(os.getenv("SHADOW_EXECUTION_TAKE_PROFIT_PCT", "25"))
    except Exception:
        return 25.0


def _stop_loss_pct() -> float:
    try:
        return float(os.getenv("SHADOW_EXECUTION_STOP_LOSS_PCT", "12"))
    except Exception:
        return 12.0


def _max_hold_minutes() -> int:
    try:
        return max(1, int(os.getenv("SHADOW_EXECUTION_MAX_HOLD_MINUTES", "60")))
    except Exception:
        return 60


def enabled() -> bool:
    return _env_bool("ENABLE_SHADOW_EXECUTION", "1")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _build_decision_context(event) -> dict[str, Any]:
    extra = event.extra if isinstance(event.extra, dict) else {}
    return {
        "event_type": event.type,
        "source": event.source,
        "token": event.token,
        "creator": event.creator,
        "confidence": event.confidence,
        "reasons": list(event.reasons) if isinstance(event.reasons, list) else [],
        "lifecycle": extra.get("lifecycle"),
        "attention_score": extra.get("attention_score"),
        "risk_score": extra.get("risk_score"),
        "elite_score": extra.get("elite_score"),
        "dex_summary": extra.get("dex_summary") if isinstance(extra.get("dex_summary"), dict) else {},
        "trade_validation": extra.get("trade_validation") if isinstance(extra.get("trade_validation"), dict) else {},
    }


def init() -> None:
    global _SCHEMA_READY
    with _connect() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_positions (
                position_id TEXT PRIMARY KEY,
                signal_id TEXT,
                token TEXT NOT NULL,
                source_event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                closed_ts INTEGER,
                intended_size_usd REAL NOT NULL,
                position_size_tokens REAL NOT NULL,
                pair_address TEXT,
                dex_id TEXT,
                entry_mid_price_usd REAL,
                entry_exec_price_usd REAL,
                expected_buy_slippage_bps REAL,
                expected_sell_slippage_bps REAL,
                take_profit_pct REAL NOT NULL,
                stop_loss_pct REAL NOT NULL,
                max_hold_minutes INTEGER NOT NULL,
                latest_price_usd REAL,
                latest_liquidity_usd REAL,
                latest_exit_value_usd REAL,
                latest_pnl_pct REAL,
                latest_pnl_usd REAL,
                peak_pnl_pct REAL,
                trough_pnl_pct REAL,
                exit_reason TEXT,
                exit_price_usd REAL,
                exit_value_usd REAL,
                validation_json TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_position_marks (
                position_id TEXT NOT NULL,
                observed_ts INTEGER NOT NULL,
                price_usd REAL,
                liquidity_usd REAL,
                exit_value_usd REAL,
                pnl_pct REAL,
                pnl_usd REAL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (position_id, observed_ts)
            )
            """
        )
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shadow_positions_signal ON shadow_positions(signal_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_shadow_positions_status ON shadow_positions(status, updated_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_shadow_marks_position ON shadow_position_marks(position_id, observed_ts)")
    _SCHEMA_READY = True


def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    init()


def open_shadow_position(event) -> str | None:
    _ensure_schema()
    extra = event.extra if isinstance(event.extra, dict) else {}
    validation = extra.get("trade_validation") if isinstance(extra.get("trade_validation"), dict) else {}
    if not validation or not validation.get("approved"):
        logger.info("[shadow-exec-skip] token=%s reason=validation_not_approved", event.token)
        return None
    now = int(time.time())
    quote_expires_ts = int(validation.get("quote_expires_ts") or 0)
    if quote_expires_ts and now > quote_expires_ts:
        logger.warning(
            "[shadow-exec-skip] token=%s reason=validation_quote_expired expired_ts=%s now_ts=%s",
            event.token,
            quote_expires_ts,
            now,
        )
        return None

    buy_quote = validation.get("buy_quote") if isinstance(validation.get("buy_quote"), dict) else {}
    signal_id = str(extra.get("_signal_id") or "").strip() or None
    position_size_tokens = float(buy_quote.get("expected_output_tokens") or 0.0)
    entry_exec_price = float(buy_quote.get("execution_price_usd") or 0.0)
    entry_mid_price = float((extra.get("dex_summary") or {}).get("price_usd") or 0.0)
    if position_size_tokens <= 0 or entry_exec_price <= 0:
        logger.warning("[shadow-exec-skip] token=%s reason=missing_entry_quote", event.token)
        return None

    with _connect() as c:
        if signal_id:
            existing = c.execute("SELECT position_id FROM shadow_positions WHERE signal_id=?", (signal_id,)).fetchone()
            if existing is not None:
                return str(existing[0])

        position_id = uuid.uuid4().hex
        position = ShadowPosition(
            position_id=position_id,
            signal_id=signal_id,
            token=str(event.token or ""),
            source_event_type=str(event.type or ""),
            status="open",
            opened_ts=now,
            intended_size_usd=float(validation.get("intended_size_usd") or 0.0),
            position_size_tokens=position_size_tokens,
            pair_address=str(validation.get("pair_address") or "") or None,
            dex_id=str(validation.get("dex_id") or "") or None,
            entry_mid_price_usd=entry_mid_price or None,
            entry_exec_price_usd=entry_exec_price,
            expected_buy_slippage_bps=float(buy_quote.get("slippage_bps") or 0.0),
            expected_sell_slippage_bps=float(((validation.get("sell_quote") or {}) if isinstance(validation.get("sell_quote"), dict) else {}).get("slippage_bps") or 0.0),
            take_profit_pct=_take_profit_pct(),
            stop_loss_pct=_stop_loss_pct(),
            max_hold_minutes=_max_hold_minutes(),
            decision_context=_build_decision_context(event),
        )
        payload = position.as_dict()
        c.execute(
            """
            INSERT INTO shadow_positions (
                position_id, signal_id, token, source_event_type, status, opened_ts, updated_ts,
                intended_size_usd, position_size_tokens, pair_address, dex_id,
                entry_mid_price_usd, entry_exec_price_usd, expected_buy_slippage_bps,
                expected_sell_slippage_bps, take_profit_pct, stop_loss_pct, max_hold_minutes,
                latest_price_usd, latest_liquidity_usd, latest_exit_value_usd, latest_pnl_pct,
                latest_pnl_usd, peak_pnl_pct, trough_pnl_pct, validation_json, payload_json
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                signal_id,
                event.token,
                event.type,
                now,
                now,
                position.intended_size_usd,
                position.position_size_tokens,
                position.pair_address,
                position.dex_id,
                position.entry_mid_price_usd,
                position.entry_exec_price_usd,
                position.expected_buy_slippage_bps,
                position.expected_sell_slippage_bps,
                position.take_profit_pct,
                position.stop_loss_pct,
                position.max_hold_minutes,
                entry_mid_price or entry_exec_price,
                float((extra.get("dex_summary") or {}).get("liquidity_usd") or 0.0),
                position.intended_size_usd,
                0.0,
                0.0,
                0.0,
                0.0,
                _json_dumps(validation),
                _json_dumps(payload),
            ),
        )
        c.execute(
            """
            INSERT INTO shadow_position_marks (
                position_id, observed_ts, price_usd, liquidity_usd, exit_value_usd, pnl_pct, pnl_usd, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                now,
                entry_mid_price or entry_exec_price,
                float((extra.get("dex_summary") or {}).get("liquidity_usd") or 0.0),
                float(validation.get("intended_size_usd") or 0.0),
                0.0,
                0.0,
                "open",
                _json_dumps(payload),
            ),
        )
    logger.info(
        "[shadow-exec-open] token=%s position_id=%s signal_id=%s size_usd=%.2f entry_exec=%.8f qty=%.8f pair=%s",
        event.token,
        position_id,
        signal_id or "",
        float(validation.get("intended_size_usd") or 0.0),
        entry_exec_price,
        position_size_tokens,
        validation.get("pair_address") or "",
    )
    return position_id


async def _fetch_market_snapshot(token: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    dex_data = await dex_enrich_token(token)
    best_pair = select_best_pair(dex_data if isinstance(dex_data, dict) else {}, token)
    if not best_pair:
        return None, None
    return best_pair, summarize_pair(best_pair)


def _fetch_open_positions(limit: int = 25) -> list[ShadowPosition]:
    _ensure_schema()
    with _connect() as c:
        rows = c.execute(
            """
            SELECT position_id, signal_id, token, source_event_type, status, opened_ts,
                   intended_size_usd, position_size_tokens, pair_address, dex_id,
                   entry_mid_price_usd, entry_exec_price_usd, expected_buy_slippage_bps,
                   expected_sell_slippage_bps, take_profit_pct, stop_loss_pct, max_hold_minutes, payload_json
            FROM shadow_positions
            WHERE status='open'
            ORDER BY updated_ts ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    positions: list[ShadowPosition] = []
    for row in rows:
        payload = {}
        try:
            payload = json.loads(row["payload_json"] or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        positions.append(
            ShadowPosition(
                position_id=str(row["position_id"]),
                signal_id=str(row["signal_id"]) if row["signal_id"] is not None else None,
                token=str(row["token"]),
                source_event_type=str(row["source_event_type"]),
                status=str(row["status"]),
                opened_ts=int(row["opened_ts"]),
                intended_size_usd=float(row["intended_size_usd"] or 0.0),
                position_size_tokens=float(row["position_size_tokens"] or 0.0),
                pair_address=str(row["pair_address"]) if row["pair_address"] is not None else None,
                dex_id=str(row["dex_id"]) if row["dex_id"] is not None else None,
                entry_mid_price_usd=float(row["entry_mid_price_usd"]) if row["entry_mid_price_usd"] is not None else None,
                entry_exec_price_usd=float(row["entry_exec_price_usd"] or 0.0),
                expected_buy_slippage_bps=float(row["expected_buy_slippage_bps"] or 0.0),
                expected_sell_slippage_bps=float(row["expected_sell_slippage_bps"] or 0.0),
                take_profit_pct=float(row["take_profit_pct"] or 0.0),
                stop_loss_pct=float(row["stop_loss_pct"] or 0.0),
                max_hold_minutes=int(row["max_hold_minutes"] or 0),
                decision_context=payload.get("decision_context") if isinstance(payload.get("decision_context"), dict) else {},
            )
        )
    return positions


def _write_mark(
    *,
    position_id: str,
    observed_ts: int,
    price_usd: float | None,
    liquidity_usd: float | None,
    exit_value_usd: float | None,
    pnl_pct: float | None,
    pnl_usd: float | None,
    status: str,
    payload: dict[str, Any],
) -> None:
    with _connect() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO shadow_position_marks (
                position_id, observed_ts, price_usd, liquidity_usd, exit_value_usd, pnl_pct, pnl_usd, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                observed_ts,
                price_usd,
                liquidity_usd,
                exit_value_usd,
                pnl_pct,
                pnl_usd,
                status,
                _json_dumps(payload),
            ),
        )


async def refresh_open_position(position: ShadowPosition) -> None:
    token = position.token
    position_id = position.position_id
    if not token or not position_id:
        return
    best_pair, dex_summary = await _fetch_market_snapshot(token)
    if not best_pair or not dex_summary:
        logger.warning("[shadow-exec-refresh-skip] token=%s position_id=%s reason=market_unavailable", token, position_id)
        return
    ctx = build_pair_context(best_pair, token)
    if ctx is None:
        logger.warning("[shadow-exec-refresh-skip] token=%s position_id=%s reason=pair_context_invalid", token, position_id)
        return
    token_amount = position.position_size_tokens
    intended_size_usd = position.intended_size_usd
    sell_quote = simulate_sell_quote(ctx, token_amount)
    if sell_quote is None:
        logger.warning("[shadow-exec-refresh-skip] token=%s position_id=%s reason=sell_quote_unavailable", token, position_id)
        return

    now = int(time.time())
    exit_value_usd = float(sell_quote.expected_output_usd)
    pnl_usd = exit_value_usd - intended_size_usd
    pnl_pct = (pnl_usd / intended_size_usd) * 100.0 if intended_size_usd > 0 else 0.0
    age_minutes = max(0.0, (now - position.opened_ts) / 60.0)
    take_profit_pct = position.take_profit_pct or _take_profit_pct()
    stop_loss_pct = position.stop_loss_pct or _stop_loss_pct()
    max_hold_minutes = position.max_hold_minutes or _max_hold_minutes()

    exit_reason = None
    if pnl_pct >= take_profit_pct:
        exit_reason = "take_profit"
    elif pnl_pct <= (-1.0 * stop_loss_pct):
        exit_reason = "stop_loss"
    elif age_minutes >= max_hold_minutes:
        exit_reason = "time_stop"

    payload = {
        "token": token,
        "position_id": position_id,
        "market_data": {
            "snapshot_ts": dex_summary.get("snapshot_ts"),
            "age_sec": 0.0 if dex_summary.get("snapshot_ts") else None,
        },
        "price_usd": dex_summary.get("price_usd"),
        "liquidity_usd": dex_summary.get("liquidity_usd"),
        "exit_value_usd": exit_value_usd,
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "sell_quote": sell_quote.as_dict(),
        "exit_reason": exit_reason,
        "age_minutes": round(age_minutes, 2),
        "decision_context": position.decision_context,
    }

    with _connect() as c:
        existing = c.execute("SELECT peak_pnl_pct, trough_pnl_pct FROM shadow_positions WHERE position_id=?", (position_id,)).fetchone()
        peak = max(float(existing[0] if existing and existing[0] is not None else pnl_pct), pnl_pct)
        trough = min(float(existing[1] if existing and existing[1] is not None else pnl_pct), pnl_pct)
        if exit_reason:
            c.execute(
                """
                UPDATE shadow_positions
                SET status='closed', updated_ts=?, closed_ts=?, latest_price_usd=?, latest_liquidity_usd=?,
                    latest_exit_value_usd=?, latest_pnl_pct=?, latest_pnl_usd=?, peak_pnl_pct=?, trough_pnl_pct=?,
                    exit_reason=?, exit_price_usd=?, exit_value_usd=?, payload_json=?
                WHERE position_id=?
                """,
                (
                    now,
                    now,
                    dex_summary.get("price_usd"),
                    dex_summary.get("liquidity_usd"),
                    exit_value_usd,
                    pnl_pct,
                    pnl_usd,
                    peak,
                    trough,
                    exit_reason,
                    sell_quote.execution_price_usd,
                    exit_value_usd,
                    _json_dumps(payload),
                    position_id,
                ),
            )
        else:
            c.execute(
                """
                UPDATE shadow_positions
                SET updated_ts=?, latest_price_usd=?, latest_liquidity_usd=?, latest_exit_value_usd=?,
                    latest_pnl_pct=?, latest_pnl_usd=?, peak_pnl_pct=?, trough_pnl_pct=?, payload_json=?
                WHERE position_id=?
                """,
                (
                    now,
                    dex_summary.get("price_usd"),
                    dex_summary.get("liquidity_usd"),
                    exit_value_usd,
                    pnl_pct,
                    pnl_usd,
                    peak,
                    trough,
                    _json_dumps(payload),
                    position_id,
                ),
            )
    _write_mark(
        position_id=position_id,
        observed_ts=now,
        price_usd=float(dex_summary.get("price_usd") or 0.0),
        liquidity_usd=float(dex_summary.get("liquidity_usd") or 0.0),
        exit_value_usd=exit_value_usd,
        pnl_pct=pnl_pct,
        pnl_usd=pnl_usd,
        status="closed" if exit_reason else "open",
        payload=payload,
    )
    if exit_reason:
        logger.info(
            "[shadow-exec-close] token=%s position_id=%s reason=%s pnl_pct=%.2f pnl_usd=%.2f",
            token,
            position_id,
            exit_reason,
            pnl_pct,
            pnl_usd,
        )
    else:
        logger.info(
            "[shadow-exec-mark] token=%s position_id=%s pnl_pct=%.2f pnl_usd=%.2f age_minutes=%.2f",
            token,
            position_id,
            pnl_pct,
            pnl_usd,
            age_minutes,
        )


async def shadow_monitor_worker() -> None:
    _ensure_schema()
    while True:
        if not enabled():
            await asyncio.sleep(_poll_seconds())
            continue
        positions = _fetch_open_positions(limit=25)
        if not positions:
            await asyncio.sleep(_poll_seconds())
            continue
        for position in positions:
            try:
                logger.info("[shadow-exec-monitor] token=%s position_id=%s status=%s", position.token, position.position_id, position.status)
                await refresh_open_position(position)
            except Exception:
                logger.exception("[shadow-exec-refresh-error] token=%s position_id=%s", position.token, position.position_id)
        await asyncio.sleep(_poll_seconds())

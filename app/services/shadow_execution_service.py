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
from worker.metadata import fetch_token_metadata
from worker.route_quote import resolve_sell_quote
from worker.trade_validator import build_pair_context, simulate_sell_quote
from worker.config import (
    SHADOW_EXECUTION_ENTRY_FEE_BPS,
    SHADOW_EXECUTION_EXIT_FEE_BPS,
    SHADOW_EXECUTION_FIXED_ENTRY_COST_USD,
    SHADOW_EXECUTION_FIXED_EXIT_COST_USD,
)
from worker.execution_lifecycle import (
    STATE_CLOSED,
    STATE_ENTRY_RECORDED,
    STATE_MONITOR_ERROR,
    plan_shadow_monitor_transition,
)


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
    execution_state: str
    opened_ts: int
    intended_size_usd: float
    position_size_tokens: float
    pair_address: str | None
    dex_id: str | None
    entry_mid_price_usd: float | None
    entry_exec_price_usd: float
    entry_fee_usd: float
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


def _entry_fee_bps() -> float:
    try:
        return max(0.0, float(os.getenv("SHADOW_EXECUTION_ENTRY_FEE_BPS", str(SHADOW_EXECUTION_ENTRY_FEE_BPS))))
    except Exception:
        return max(0.0, float(SHADOW_EXECUTION_ENTRY_FEE_BPS))


def _exit_fee_bps() -> float:
    try:
        return max(0.0, float(os.getenv("SHADOW_EXECUTION_EXIT_FEE_BPS", str(SHADOW_EXECUTION_EXIT_FEE_BPS))))
    except Exception:
        return max(0.0, float(SHADOW_EXECUTION_EXIT_FEE_BPS))


def _fixed_entry_cost_usd() -> float:
    try:
        return max(0.0, float(os.getenv("SHADOW_EXECUTION_FIXED_ENTRY_COST_USD", str(SHADOW_EXECUTION_FIXED_ENTRY_COST_USD))))
    except Exception:
        return max(0.0, float(SHADOW_EXECUTION_FIXED_ENTRY_COST_USD))


def _fixed_exit_cost_usd() -> float:
    try:
        return max(0.0, float(os.getenv("SHADOW_EXECUTION_FIXED_EXIT_COST_USD", str(SHADOW_EXECUTION_FIXED_EXIT_COST_USD))))
    except Exception:
        return max(0.0, float(SHADOW_EXECUTION_FIXED_EXIT_COST_USD))


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


def _ensure_column(c: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _calculate_entry_fee_usd(intended_size_usd: float) -> float:
    return round((max(0.0, intended_size_usd) * (_entry_fee_bps() / 10000.0)) + _fixed_entry_cost_usd(), 6)


def _calculate_exit_fee_usd(exit_value_usd: float) -> float:
    return round((max(0.0, exit_value_usd) * (_exit_fee_bps() / 10000.0)) + _fixed_exit_cost_usd(), 6)


def _net_pnl(exit_value_usd: float, intended_size_usd: float, entry_fee_usd: float) -> tuple[float, float, float]:
    exit_fee_usd = _calculate_exit_fee_usd(exit_value_usd)
    net_exit_value_usd = exit_value_usd - exit_fee_usd
    net_pnl_usd = net_exit_value_usd - intended_size_usd - entry_fee_usd
    return net_exit_value_usd, net_pnl_usd, exit_fee_usd


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
                execution_state TEXT NOT NULL DEFAULT 'entry_recorded',
                opened_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                closed_ts INTEGER,
                intended_size_usd REAL NOT NULL,
                position_size_tokens REAL NOT NULL,
                pair_address TEXT,
                dex_id TEXT,
                entry_mid_price_usd REAL,
                entry_exec_price_usd REAL,
                entry_fee_usd REAL NOT NULL DEFAULT 0,
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
                latest_net_exit_value_usd REAL,
                latest_net_pnl_pct REAL,
                latest_net_pnl_usd REAL,
                latest_exit_fee_usd REAL,
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
                net_exit_value_usd REAL,
                net_pnl_pct REAL,
                net_pnl_usd REAL,
                exit_fee_usd REAL,
                execution_state TEXT,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (position_id, observed_ts)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_execution_transitions (
                transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT NOT NULL,
                observed_ts INTEGER NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                transition_reason TEXT NOT NULL,
                terminal INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL
            )
            """
        )
        _ensure_column(c, "shadow_positions", "execution_state", "TEXT NOT NULL DEFAULT 'entry_recorded'")
        _ensure_column(c, "shadow_positions", "entry_fee_usd", "REAL NOT NULL DEFAULT 0")
        _ensure_column(c, "shadow_positions", "latest_net_exit_value_usd", "REAL")
        _ensure_column(c, "shadow_positions", "latest_net_pnl_pct", "REAL")
        _ensure_column(c, "shadow_positions", "latest_net_pnl_usd", "REAL")
        _ensure_column(c, "shadow_positions", "latest_exit_fee_usd", "REAL")
        _ensure_column(c, "shadow_position_marks", "net_exit_value_usd", "REAL")
        _ensure_column(c, "shadow_position_marks", "net_pnl_pct", "REAL")
        _ensure_column(c, "shadow_position_marks", "net_pnl_usd", "REAL")
        _ensure_column(c, "shadow_position_marks", "exit_fee_usd", "REAL")
        _ensure_column(c, "shadow_position_marks", "execution_state", "TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shadow_positions_signal ON shadow_positions(signal_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_shadow_positions_status ON shadow_positions(status, updated_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_shadow_marks_position ON shadow_position_marks(position_id, observed_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_shadow_transitions_position ON shadow_execution_transitions(position_id, observed_ts)")
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
    entry_fee_usd = _calculate_entry_fee_usd(float(validation.get("intended_size_usd") or 0.0))
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
            execution_state=STATE_ENTRY_RECORDED,
            opened_ts=now,
            intended_size_usd=float(validation.get("intended_size_usd") or 0.0),
            position_size_tokens=position_size_tokens,
            pair_address=str(validation.get("pair_address") or "") or None,
            dex_id=str(validation.get("dex_id") or "") or None,
            entry_mid_price_usd=entry_mid_price or None,
            entry_exec_price_usd=entry_exec_price,
            entry_fee_usd=entry_fee_usd,
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
                position_id, signal_id, token, source_event_type, status, execution_state, opened_ts, updated_ts,
                intended_size_usd, position_size_tokens, pair_address, dex_id,
                entry_mid_price_usd, entry_exec_price_usd, entry_fee_usd, expected_buy_slippage_bps,
                expected_sell_slippage_bps, take_profit_pct, stop_loss_pct, max_hold_minutes,
                latest_price_usd, latest_liquidity_usd, latest_exit_value_usd, latest_pnl_pct,
                latest_pnl_usd, latest_net_exit_value_usd, latest_net_pnl_pct, latest_net_pnl_usd, latest_exit_fee_usd,
                peak_pnl_pct, trough_pnl_pct, validation_json, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                signal_id,
                event.token,
                event.type,
                position.status,
                position.execution_state,
                now,
                now,
                position.intended_size_usd,
                position.position_size_tokens,
                position.pair_address,
                position.dex_id,
                position.entry_mid_price_usd,
                position.entry_exec_price_usd,
                position.entry_fee_usd,
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
                position.intended_size_usd,
                ((0.0 - position.entry_fee_usd) / position.intended_size_usd * 100.0) if position.intended_size_usd > 0 else 0.0,
                0.0 - position.entry_fee_usd,
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
                position_id, observed_ts, price_usd, liquidity_usd, exit_value_usd, pnl_pct, pnl_usd,
                net_exit_value_usd, net_pnl_pct, net_pnl_usd, exit_fee_usd, execution_state, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                now,
                entry_mid_price or entry_exec_price,
                float((extra.get("dex_summary") or {}).get("liquidity_usd") or 0.0),
                float(validation.get("intended_size_usd") or 0.0),
                0.0,
                0.0,
                float(validation.get("intended_size_usd") or 0.0),
                ((0.0 - position.entry_fee_usd) / position.intended_size_usd * 100.0) if position.intended_size_usd > 0 else 0.0,
                0.0 - position.entry_fee_usd,
                0.0,
                position.execution_state,
                "open",
                _json_dumps(payload),
            ),
        )
    logger.info(
        "[shadow-exec-open] token=%s position_id=%s signal_id=%s size_usd=%.2f entry_exec=%.8f qty=%.8f pair=%s entry_fee_usd=%.4f state=%s",
        event.token,
        position_id,
        signal_id or "",
        float(validation.get("intended_size_usd") or 0.0),
        entry_exec_price,
        position_size_tokens,
        validation.get("pair_address") or "",
        entry_fee_usd,
        position.execution_state,
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
                   execution_state, intended_size_usd, position_size_tokens, pair_address, dex_id,
                   entry_mid_price_usd, entry_exec_price_usd, expected_buy_slippage_bps,
                   expected_sell_slippage_bps, take_profit_pct, stop_loss_pct, max_hold_minutes, entry_fee_usd, payload_json
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
                execution_state=str(row["execution_state"] or STATE_ENTRY_RECORDED),
                opened_ts=int(row["opened_ts"]),
                intended_size_usd=float(row["intended_size_usd"] or 0.0),
                position_size_tokens=float(row["position_size_tokens"] or 0.0),
                pair_address=str(row["pair_address"]) if row["pair_address"] is not None else None,
                dex_id=str(row["dex_id"]) if row["dex_id"] is not None else None,
                entry_mid_price_usd=float(row["entry_mid_price_usd"]) if row["entry_mid_price_usd"] is not None else None,
                entry_exec_price_usd=float(row["entry_exec_price_usd"] or 0.0),
                entry_fee_usd=float(row["entry_fee_usd"] or 0.0),
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
    net_exit_value_usd: float | None,
    net_pnl_pct: float | None,
    net_pnl_usd: float | None,
    exit_fee_usd: float | None,
    execution_state: str,
    status: str,
    payload: dict[str, Any],
) -> None:
    with _connect() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO shadow_position_marks (
                position_id, observed_ts, price_usd, liquidity_usd, exit_value_usd, pnl_pct, pnl_usd,
                net_exit_value_usd, net_pnl_pct, net_pnl_usd, exit_fee_usd, execution_state, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                observed_ts,
                price_usd,
                liquidity_usd,
                exit_value_usd,
                pnl_pct,
                pnl_usd,
                net_exit_value_usd,
                net_pnl_pct,
                net_pnl_usd,
                exit_fee_usd,
                execution_state,
                status,
                _json_dumps(payload),
            ),
        )


def _record_transitions(*, position_id: str, observed_ts: int, plan: dict[str, Any], payload: dict[str, Any]) -> None:
    transitions = plan.get("transitions") if isinstance(plan, dict) else None
    if not isinstance(transitions, list) or not transitions:
        return
    with _connect() as c:
        for item in transitions:
            if not isinstance(item, dict):
                continue
            c.execute(
                """
                INSERT INTO shadow_execution_transitions (
                    position_id, observed_ts, from_state, to_state, transition_reason, terminal, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    observed_ts,
                    str(item.get("from_state") or ""),
                    str(item.get("to_state") or ""),
                    str(item.get("reason") or ""),
                    1 if item.get("terminal") else 0,
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
        plan = plan_shadow_monitor_transition(position.execution_state, exit_reason=None, monitor_error=True)
        _record_transitions(
            position_id=position_id,
            observed_ts=int(time.time()),
            plan=plan.as_dict(),
            payload={"token": token, "position_id": position_id, "reason": "market_unavailable"},
        )
        logger.warning("[shadow-exec-refresh-skip] token=%s position_id=%s reason=market_unavailable", token, position_id)
        return
    ctx = build_pair_context(best_pair, token)
    if ctx is None:
        plan = plan_shadow_monitor_transition(position.execution_state, exit_reason=None, monitor_error=True)
        _record_transitions(
            position_id=position_id,
            observed_ts=int(time.time()),
            plan=plan.as_dict(),
            payload={"token": token, "position_id": position_id, "reason": "pair_context_invalid"},
        )
        logger.warning("[shadow-exec-refresh-skip] token=%s position_id=%s reason=pair_context_invalid", token, position_id)
        return
    token_amount = position.position_size_tokens
    intended_size_usd = position.intended_size_usd
    reserve_sell_quote = simulate_sell_quote(ctx, token_amount)
    token_meta = fetch_token_metadata(token)
    sell_quote_result = resolve_sell_quote(
        token=token,
        token_meta=token_meta,
        best_pair=best_pair,
        ctx=ctx,
        reserve_fallback_quote=reserve_sell_quote.as_dict() if reserve_sell_quote is not None else None,
        token_amount=token_amount,
    )
    sell_quote_payload = sell_quote_result.quote
    if sell_quote_payload is None:
        plan = plan_shadow_monitor_transition(position.execution_state, exit_reason=None, monitor_error=True)
        _record_transitions(
            position_id=position_id,
            observed_ts=int(time.time()),
            plan=plan.as_dict(),
            payload={"token": token, "position_id": position_id, "reason": "sell_quote_unavailable"},
        )
        logger.warning("[shadow-exec-refresh-skip] token=%s position_id=%s reason=sell_quote_unavailable", token, position_id)
        return

    now = int(time.time())
    exit_value_usd = float(sell_quote_payload.get("expected_output_usd") or 0.0)
    pnl_usd = exit_value_usd - intended_size_usd
    pnl_pct = (pnl_usd / intended_size_usd) * 100.0 if intended_size_usd > 0 else 0.0
    net_exit_value_usd, net_pnl_usd, exit_fee_usd = _net_pnl(exit_value_usd, intended_size_usd, position.entry_fee_usd)
    net_pnl_pct = (net_pnl_usd / intended_size_usd) * 100.0 if intended_size_usd > 0 else 0.0
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

    transition_plan = plan_shadow_monitor_transition(position.execution_state, exit_reason=exit_reason)
    plan_payload = transition_plan.as_dict()
    next_state = transition_plan.next_state
    transition_chain = [position.execution_state] + [item["to_state"] for item in plan_payload["transitions"]]
    if plan_payload["transitions"]:
        logger.info(
            "[shadow-exec-transition] token=%s position_id=%s from=%s to=%s chain=%s reason=%s",
            token,
            position_id,
            position.execution_state,
            next_state,
            transition_chain,
            exit_reason or "mark_to_market",
        )

    payload = {
        "token": token,
        "position_id": position_id,
        "execution": {
            "state": next_state,
            "transition_chain": transition_chain,
            "entry_fee_usd": position.entry_fee_usd,
            "exit_fee_usd": exit_fee_usd,
        },
        "market_data": {
            "snapshot_ts": dex_summary.get("snapshot_ts"),
            "age_sec": 0.0 if dex_summary.get("snapshot_ts") else None,
        },
        "price_usd": dex_summary.get("price_usd"),
        "liquidity_usd": dex_summary.get("liquidity_usd"),
        "exit_value_usd": exit_value_usd,
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "net_exit_value_usd": net_exit_value_usd,
        "net_pnl_pct": net_pnl_pct,
        "net_pnl_usd": net_pnl_usd,
        "sell_quote": sell_quote_payload,
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
                SET status='closed', execution_state=?, updated_ts=?, closed_ts=?, latest_price_usd=?, latest_liquidity_usd=?,
                    latest_exit_value_usd=?, latest_pnl_pct=?, latest_pnl_usd=?, latest_net_exit_value_usd=?, latest_net_pnl_pct=?,
                    latest_net_pnl_usd=?, latest_exit_fee_usd=?, peak_pnl_pct=?, trough_pnl_pct=?,
                    exit_reason=?, exit_price_usd=?, exit_value_usd=?, payload_json=?
                WHERE position_id=?
                """,
                (
                    next_state,
                    now,
                    now,
                    dex_summary.get("price_usd"),
                    dex_summary.get("liquidity_usd"),
                    exit_value_usd,
                    pnl_pct,
                    pnl_usd,
                    net_exit_value_usd,
                    net_pnl_pct,
                    net_pnl_usd,
                    exit_fee_usd,
                    peak,
                    trough,
                    exit_reason,
                    sell_quote_payload.get("execution_price_usd"),
                    exit_value_usd,
                    _json_dumps(payload),
                    position_id,
                ),
            )
        else:
            c.execute(
                """
                UPDATE shadow_positions
                SET execution_state=?, updated_ts=?, latest_price_usd=?, latest_liquidity_usd=?, latest_exit_value_usd=?,
                    latest_pnl_pct=?, latest_pnl_usd=?, latest_net_exit_value_usd=?, latest_net_pnl_pct=?,
                    latest_net_pnl_usd=?, latest_exit_fee_usd=?, peak_pnl_pct=?, trough_pnl_pct=?, payload_json=?
                WHERE position_id=?
                """,
                (
                    next_state,
                    now,
                    dex_summary.get("price_usd"),
                    dex_summary.get("liquidity_usd"),
                    exit_value_usd,
                    pnl_pct,
                    pnl_usd,
                    net_exit_value_usd,
                    net_pnl_pct,
                    net_pnl_usd,
                    exit_fee_usd,
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
        net_exit_value_usd=net_exit_value_usd,
        net_pnl_pct=net_pnl_pct,
        net_pnl_usd=net_pnl_usd,
        exit_fee_usd=exit_fee_usd,
        execution_state=next_state,
        status="closed" if exit_reason else "open",
        payload=payload,
    )
    _record_transitions(
        position_id=position_id,
        observed_ts=now,
        plan=plan_payload,
        payload=payload,
    )
    if exit_reason:
        logger.info(
            "[shadow-exec-close] token=%s position_id=%s reason=%s gross_pnl_pct=%.2f gross_pnl_usd=%.2f net_pnl_pct=%.2f net_pnl_usd=%.2f exit_fee_usd=%.4f",
            token,
            position_id,
            exit_reason,
            pnl_pct,
            pnl_usd,
            net_pnl_pct,
            net_pnl_usd,
            exit_fee_usd,
        )
    else:
        logger.info(
            "[shadow-exec-mark] token=%s position_id=%s gross_pnl_pct=%.2f gross_pnl_usd=%.2f net_pnl_pct=%.2f net_pnl_usd=%.2f age_minutes=%.2f state=%s",
            token,
            position_id,
            pnl_pct,
            pnl_usd,
            net_pnl_pct,
            net_pnl_usd,
            age_minutes,
            next_state,
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

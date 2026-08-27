from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from app.models.position import (
    CATALYST_STATES,
    EXIT_STYLE_CATALYST_RUNNER,
    EXECUTION_MODE_MANUAL,
    POSITION_CLOSED,
    POSITION_OPEN,
    RISK_PROFILE_AGGRESSIVE,
)
from app.services.db_service import connect_sqlite, resolve_engine_db_path


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        raw = json.loads(value)
    except Exception:
        return []
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


class ManualPositionService:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else resolve_engine_db_path()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.db_path)

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS manual_positions (
                    position_id TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    symbol TEXT,
                    status TEXT NOT NULL,
                    risk_profile TEXT NOT NULL,
                    exit_style TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    catalyst_mode INTEGER NOT NULL DEFAULT 0,
                    original_token_quantity REAL NOT NULL DEFAULT 0,
                    current_token_quantity REAL NOT NULL DEFAULT 0,
                    total_cash_invested_usd REAL NOT NULL DEFAULT 0,
                    total_sol_invested REAL NOT NULL DEFAULT 0,
                    total_fees_usd REAL NOT NULL DEFAULT 0,
                    average_entry_price_usd REAL NOT NULL DEFAULT 0,
                    first_entry_ts INTEGER,
                    most_recent_entry_ts INTEGER,
                    realized_proceeds_usd REAL NOT NULL DEFAULT 0,
                    realized_profit_usd REAL NOT NULL DEFAULT 0,
                    remaining_unrecovered_principal_usd REAL NOT NULL DEFAULT 0,
                    current_executable_position_value_usd REAL,
                    current_executable_return_pct REAL,
                    highest_executable_position_value_usd REAL NOT NULL DEFAULT 0,
                    peak_executable_return_pct REAL NOT NULL DEFAULT 0,
                    drawdown_from_executable_peak_pct REAL NOT NULL DEFAULT 0,
                    original_thesis TEXT,
                    invalidation_conditions_json TEXT NOT NULL DEFAULT '[]',
                    catalyst_id TEXT,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL,
                    closed_ts INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_manual_positions_token_status ON manual_positions(token, status);
                CREATE INDEX IF NOT EXISTS idx_manual_positions_updated ON manual_positions(updated_ts);

                CREATE TABLE IF NOT EXISTS manual_position_fills (
                    fill_id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    token_quantity REAL NOT NULL,
                    gross_usd REAL NOT NULL DEFAULT 0,
                    gross_sol REAL NOT NULL DEFAULT 0,
                    net_amount_usd REAL NOT NULL DEFAULT 0,
                    execution_price_usd REAL NOT NULL DEFAULT 0,
                    fees_usd REAL NOT NULL DEFAULT 0,
                    slippage_pct REAL,
                    price_impact_pct REAL,
                    tx_signature TEXT,
                    fill_ts INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    notes TEXT,
                    created_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_manual_fills_position_ts ON manual_position_fills(position_id, fill_ts);
                CREATE INDEX IF NOT EXISTS idx_manual_fills_signature ON manual_position_fills(tx_signature);

                CREATE TABLE IF NOT EXISTS manual_catalysts (
                    catalyst_id TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    catalyst_type TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    original_source TEXT,
                    secondary_confirmations_json TEXT NOT NULL DEFAULT '[]',
                    first_observed_ts INTEGER,
                    expected_start_ts INTEGER,
                    expected_end_ts INTEGER,
                    verification_status TEXT NOT NULL,
                    market_reaction_start_price_usd REAL,
                    market_reaction_current_price_usd REAL,
                    price_change_since_catalyst_pct REAL,
                    unique_buyers_added_since_catalyst INTEGER,
                    holders_added_since_catalyst INTEGER,
                    net_sol_flow_since_catalyst REAL,
                    liquidity_change_since_catalyst_pct REAL,
                    creator_insider_sell_activity TEXT,
                    catalyst_confidence_pct REAL NOT NULL DEFAULT 0,
                    catalyst_flow_confirmation INTEGER NOT NULL DEFAULT 0,
                    catalyst_invalidation_reason TEXT,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_manual_catalysts_token_status ON manual_catalysts(token, verification_status);
                """
            )

    def mark_bought(
        self,
        *,
        token: str,
        symbol: str | None = None,
        token_quantity: float,
        gross_usd: float,
        gross_sol: float = 0.0,
        fees_usd: float = 0.0,
        execution_price_usd: float | None = None,
        risk_profile: str = RISK_PROFILE_AGGRESSIVE,
        exit_style: str = EXIT_STYLE_CATALYST_RUNNER,
        catalyst_mode: bool = False,
        original_thesis: str | None = None,
        invalidation_conditions: list[str] | None = None,
        tx_signature: str | None = None,
        fill_ts: int | None = None,
        source: str = "manual",
        notes: str | None = None,
    ) -> dict[str, Any]:
        self.init_schema()
        now = _now()
        fill_ts = int(fill_ts or now)
        position_id = uuid.uuid4().hex
        net_cost = _float(gross_usd) + _float(fees_usd)
        price = _float(execution_price_usd) or (net_cost / token_quantity if token_quantity else 0.0)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO manual_positions (
                    position_id, token, symbol, status, risk_profile, exit_style, execution_mode,
                    catalyst_mode, original_token_quantity, current_token_quantity,
                    total_cash_invested_usd, total_sol_invested, total_fees_usd,
                    average_entry_price_usd, first_entry_ts, most_recent_entry_ts,
                    realized_proceeds_usd, realized_profit_usd, remaining_unrecovered_principal_usd,
                    highest_executable_position_value_usd, original_thesis,
                    invalidation_conditions_json, created_ts, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    token,
                    symbol,
                    POSITION_OPEN,
                    risk_profile,
                    exit_style,
                    EXECUTION_MODE_MANUAL,
                    1 if catalyst_mode else 0,
                    token_quantity,
                    token_quantity,
                    net_cost,
                    gross_sol,
                    fees_usd,
                    price,
                    fill_ts,
                    fill_ts,
                    net_cost,
                    original_thesis,
                    _json(invalidation_conditions or []),
                    now,
                    now,
                ),
            )
            self._insert_fill_in_tx(
                conn,
                position_id=position_id,
                side="buy",
                token_quantity=token_quantity,
                gross_usd=gross_usd,
                gross_sol=gross_sol,
                net_amount_usd=net_cost,
                execution_price_usd=price,
                fees_usd=fees_usd,
                tx_signature=tx_signature,
                fill_ts=fill_ts,
                source=source,
                notes=notes,
            )
            conn.commit()
        return self.get_position(position_id) or {}

    def record_buy(self, position_id: str, **kwargs: Any) -> dict[str, Any]:
        self.init_schema()
        token_quantity = _float(kwargs["token_quantity"])
        gross_usd = _float(kwargs.get("gross_usd"))
        fees_usd = _float(kwargs.get("fees_usd"))
        gross_sol = _float(kwargs.get("gross_sol"))
        net_cost = gross_usd + fees_usd
        fill_ts = int(kwargs.get("fill_ts") or _now())
        price = _float(kwargs.get("execution_price_usd")) or (net_cost / token_quantity if token_quantity else 0.0)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._position_row(conn, position_id)
            if not row:
                raise KeyError(position_id)
            if row["status"] != POSITION_OPEN:
                raise ValueError("position_not_open")
            current_qty = _float(row["current_token_quantity"]) + token_quantity
            original_qty = _float(row["original_token_quantity"]) + token_quantity
            total_basis = _float(row["total_cash_invested_usd"]) + net_cost
            avg_entry = total_basis / original_qty if original_qty else 0.0
            realized = _float(row["realized_proceeds_usd"])
            conn.execute(
                """
                UPDATE manual_positions
                SET original_token_quantity=?, current_token_quantity=?, total_cash_invested_usd=?,
                    total_sol_invested=total_sol_invested + ?, total_fees_usd=total_fees_usd + ?,
                    average_entry_price_usd=?, most_recent_entry_ts=?,
                    remaining_unrecovered_principal_usd=?, updated_ts=?
                WHERE position_id=?
                """,
                (original_qty, current_qty, total_basis, gross_sol, fees_usd, avg_entry, fill_ts, max(0.0, total_basis - realized), _now(), position_id),
            )
            self._insert_fill_in_tx(
                conn,
                position_id=position_id,
                side="buy",
                token_quantity=token_quantity,
                gross_usd=gross_usd,
                gross_sol=gross_sol,
                net_amount_usd=net_cost,
                execution_price_usd=price,
                fees_usd=fees_usd,
                tx_signature=kwargs.get("tx_signature"),
                fill_ts=fill_ts,
                source=str(kwargs.get("source") or "manual"),
                notes=kwargs.get("notes"),
            )
            conn.commit()
        return self.get_position(position_id) or {}

    def record_sell(self, position_id: str, *, token_quantity: float | None = None, full: bool = False, **kwargs: Any) -> dict[str, Any]:
        self.init_schema()
        fill_ts = int(kwargs.get("fill_ts") or _now())
        gross_usd = _float(kwargs.get("gross_usd"))
        fees_usd = _float(kwargs.get("fees_usd"))
        gross_sol = _float(kwargs.get("gross_sol"))
        net_proceeds = _float(kwargs.get("net_amount_usd")) or max(0.0, gross_usd - fees_usd)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._position_row(conn, position_id)
            if not row:
                raise KeyError(position_id)
            if row["status"] != POSITION_OPEN:
                raise ValueError("position_not_open")
            current_before = _float(row["current_token_quantity"])
            sell_qty = current_before if full else _float(token_quantity)
            if sell_qty <= 0 or sell_qty > current_before + 1e-9:
                raise ValueError("invalid_sell_quantity")
            price = _float(kwargs.get("execution_price_usd")) or (net_proceeds / sell_qty if sell_qty else 0.0)
            remaining_qty = max(0.0, current_before - sell_qty)
            total_basis = _float(row["total_cash_invested_usd"])
            realized_proceeds = _float(row["realized_proceeds_usd"]) + net_proceeds
            realized_profit = realized_proceeds - min(total_basis, realized_proceeds)
            unrecovered = max(0.0, total_basis - realized_proceeds)
            closed = full or remaining_qty <= 1e-9
            conn.execute(
                """
                UPDATE manual_positions
                SET current_token_quantity=?, realized_proceeds_usd=?, realized_profit_usd=?,
                    remaining_unrecovered_principal_usd=?, total_fees_usd=total_fees_usd + ?,
                    status=?, closed_ts=?, updated_ts=?
                WHERE position_id=?
                """,
                (
                    remaining_qty,
                    realized_proceeds,
                    realized_profit,
                    unrecovered,
                    fees_usd,
                    POSITION_CLOSED if closed else POSITION_OPEN,
                    fill_ts if closed else None,
                    _now(),
                    position_id,
                ),
            )
            self._insert_fill_in_tx(
                conn,
                position_id=position_id,
                side="sell",
                token_quantity=sell_qty,
                gross_usd=gross_usd,
                gross_sol=gross_sol,
                net_amount_usd=net_proceeds,
                execution_price_usd=price,
                fees_usd=fees_usd,
                slippage_pct=kwargs.get("slippage_pct"),
                price_impact_pct=kwargs.get("price_impact_pct"),
                tx_signature=kwargs.get("tx_signature"),
                fill_ts=fill_ts,
                source=str(kwargs.get("source") or "manual"),
                notes=kwargs.get("notes"),
            )
            conn.commit()
        return self.get_position(position_id) or {}

    def update_position(self, position_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "risk_profile",
            "exit_style",
            "catalyst_mode",
            "original_thesis",
            "invalidation_conditions",
            "catalyst_id",
        }
        updates: dict[str, Any] = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_position(position_id) or {}
        sql_fields: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key == "invalidation_conditions":
                sql_fields.append("invalidation_conditions_json=?")
                values.append(_json(value or []))
            elif key == "catalyst_mode":
                sql_fields.append("catalyst_mode=?")
                values.append(1 if value else 0)
            else:
                sql_fields.append(f"{key}=?")
                values.append(value)
        sql_fields.append("updated_ts=?")
        values.append(_now())
        values.append(position_id)
        with self._connect() as conn:
            cur = conn.execute(f"UPDATE manual_positions SET {', '.join(sql_fields)} WHERE position_id=?", values)
            if cur.rowcount == 0:
                raise KeyError(position_id)
        return self.get_position(position_id) or {}

    def close_position(self, position_id: str) -> dict[str, Any]:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE manual_positions SET status=?, closed_ts=?, updated_ts=? WHERE position_id=?",
                (POSITION_CLOSED, now, now, position_id),
            )
            if cur.rowcount == 0:
                raise KeyError(position_id)
        return self.get_position(position_id) or {}

    def reopen_position(self, position_id: str) -> dict[str, Any]:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE manual_positions SET status=?, closed_ts=NULL, updated_ts=? WHERE position_id=?",
                (POSITION_OPEN, now, position_id),
            )
            if cur.rowcount == 0:
                raise KeyError(position_id)
        return self.get_position(position_id) or {}

    def attach_catalyst(self, position_id: str, catalyst_id: str) -> dict[str, Any]:
        return self.update_position(position_id, catalyst_id=catalyst_id, catalyst_mode=True)

    def create_catalyst(
        self,
        *,
        token: str,
        title: str,
        catalyst_type: str = "unknown",
        verification_status: str = "unverified",
        description: str | None = None,
        original_source: str | None = None,
        secondary_confirmations: list[str] | None = None,
        first_observed_ts: int | None = None,
        expected_start_ts: int | None = None,
        expected_end_ts: int | None = None,
        catalyst_confidence_pct: float = 0.0,
        catalyst_flow_confirmation: bool = False,
        market_reaction_start_price_usd: float | None = None,
    ) -> dict[str, Any]:
        self.init_schema()
        status = str(verification_status or "unverified").lower()
        if status not in CATALYST_STATES:
            raise ValueError("invalid_catalyst_state")
        now = _now()
        catalyst_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO manual_catalysts (
                    catalyst_id, token, catalyst_type, title, description, original_source,
                    secondary_confirmations_json, first_observed_ts, expected_start_ts,
                    expected_end_ts, verification_status, market_reaction_start_price_usd,
                    catalyst_confidence_pct, catalyst_flow_confirmation, created_ts, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    catalyst_id,
                    token,
                    catalyst_type,
                    title,
                    description,
                    original_source,
                    _json(secondary_confirmations or []),
                    first_observed_ts or now,
                    expected_start_ts,
                    expected_end_ts,
                    status,
                    market_reaction_start_price_usd,
                    catalyst_confidence_pct,
                    1 if catalyst_flow_confirmation else 0,
                    now,
                    now,
                ),
            )
        return self.get_catalyst(catalyst_id) or {}

    def update_catalyst(self, catalyst_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "verification_status",
            "market_reaction_current_price_usd",
            "price_change_since_catalyst_pct",
            "unique_buyers_added_since_catalyst",
            "holders_added_since_catalyst",
            "net_sol_flow_since_catalyst",
            "liquidity_change_since_catalyst_pct",
            "creator_insider_sell_activity",
            "catalyst_confidence_pct",
            "catalyst_flow_confirmation",
            "catalyst_invalidation_reason",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if "verification_status" in updates and str(updates["verification_status"]).lower() not in CATALYST_STATES:
            raise ValueError("invalid_catalyst_state")
        sql_fields: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key == "catalyst_flow_confirmation":
                value = 1 if value else 0
            if key == "verification_status":
                value = str(value).lower()
            sql_fields.append(f"{key}=?")
            values.append(value)
        sql_fields.append("updated_ts=?")
        values.append(_now())
        values.append(catalyst_id)
        with self._connect() as conn:
            cur = conn.execute(f"UPDATE manual_catalysts SET {', '.join(sql_fields)} WHERE catalyst_id=?", values)
            if cur.rowcount == 0:
                raise KeyError(catalyst_id)
        return self.get_catalyst(catalyst_id) or {}

    def mark_catalyst_invalid(self, catalyst_id: str, reason: str) -> dict[str, Any]:
        return self.update_catalyst(
            catalyst_id,
            verification_status="invalidated",
            catalyst_invalidation_reason=reason,
            catalyst_confidence_pct=0,
            catalyst_flow_confirmation=False,
        )

    def update_executable_value(
        self,
        position_id: str,
        *,
        executable_value_usd: float,
        quote_observed_at: int | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._position_row(conn, position_id)
            if not row:
                raise KeyError(position_id)
            total_basis = _float(row["total_cash_invested_usd"])
            realized = _float(row["realized_proceeds_usd"])
            current_value = max(0.0, _float(executable_value_usd))
            current_return = ((realized + current_value - total_basis) / total_basis * 100.0) if total_basis else 0.0
            peak_value = max(_float(row["highest_executable_position_value_usd"]), current_value)
            peak_return = max(_float(row["peak_executable_return_pct"]), current_return)
            drawdown = ((current_value - peak_value) / peak_value * 100.0) if peak_value else 0.0
            conn.execute(
                """
                UPDATE manual_positions
                SET current_executable_position_value_usd=?, current_executable_return_pct=?,
                    highest_executable_position_value_usd=?, peak_executable_return_pct=?,
                    drawdown_from_executable_peak_pct=?, updated_ts=?
                WHERE position_id=?
                """,
                (current_value, current_return, peak_value, peak_return, drawdown, now, position_id),
            )
            conn.commit()
        return self.get_position(position_id) or {}

    def get_open_position_for_token(self, token: str) -> dict[str, Any] | None:
        self.init_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM manual_positions WHERE token=? AND status=? ORDER BY created_ts DESC LIMIT 1",
                (token, POSITION_OPEN),
            ).fetchone()
        return self._row_to_position(row) if row else None

    def get_position(self, position_id: str) -> dict[str, Any] | None:
        self.init_schema()
        with self._connect() as conn:
            row = self._position_row(conn, position_id)
        return self._row_to_position(row) if row else None

    def list_fills(self, position_id: str) -> list[dict[str, Any]]:
        self.init_schema()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM manual_position_fills WHERE position_id=? ORDER BY fill_ts, created_ts",
                (position_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def position_history(self, position_id: str) -> dict[str, Any]:
        position = self.get_position(position_id)
        if not position:
            raise KeyError(position_id)
        return {"position": position, "fills": self.list_fills(position_id)}

    def get_catalyst(self, catalyst_id: str) -> dict[str, Any] | None:
        self.init_schema()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM manual_catalysts WHERE catalyst_id=?", (catalyst_id,)).fetchone()
        return self._row_to_catalyst(row) if row else None

    def get_latest_catalyst_for_token(self, token: str) -> dict[str, Any] | None:
        self.init_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM manual_catalysts WHERE token=? ORDER BY updated_ts DESC LIMIT 1",
                (token,),
            ).fetchone()
        return self._row_to_catalyst(row) if row else None

    def _position_row(self, conn: sqlite3.Connection, position_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM manual_positions WHERE position_id=?", (position_id,)).fetchone()

    def _insert_fill_in_tx(self, conn: sqlite3.Connection, **values: Any) -> str:
        fill_id = str(values.get("fill_id") or uuid.uuid4().hex)
        conn.execute(
            """
            INSERT INTO manual_position_fills (
                fill_id, position_id, side, token_quantity, gross_usd, gross_sol,
                net_amount_usd, execution_price_usd, fees_usd, slippage_pct,
                price_impact_pct, tx_signature, fill_ts, source, notes, created_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill_id,
                values["position_id"],
                values["side"],
                _float(values["token_quantity"]),
                _float(values.get("gross_usd")),
                _float(values.get("gross_sol")),
                _float(values.get("net_amount_usd")),
                _float(values.get("execution_price_usd")),
                _float(values.get("fees_usd")),
                values.get("slippage_pct"),
                values.get("price_impact_pct"),
                values.get("tx_signature"),
                int(values.get("fill_ts") or _now()),
                str(values.get("source") or "manual"),
                values.get("notes"),
                _now(),
            ),
        )
        return fill_id

    def _row_to_position(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["catalyst_mode"] = bool(item.get("catalyst_mode"))
        item["invalidation_conditions"] = _loads_list(item.pop("invalidation_conditions_json", "[]"))
        item["principal_recovered"] = _float(item.get("remaining_unrecovered_principal_usd")) <= 1e-9
        item["execution_mode"] = item.get("execution_mode") or EXECUTION_MODE_MANUAL
        return item

    def _row_to_catalyst(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["secondary_confirmations"] = _loads_list(item.pop("secondary_confirmations_json", "[]"))
        item["catalyst_flow_confirmation"] = bool(item.get("catalyst_flow_confirmation"))
        return item


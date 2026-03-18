from __future__ import annotations

import asyncio

from app.services import shadow_execution_service as ses
from worker.events import Event


def test_open_shadow_position_persists_validated_trade(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(ses, "DB_PATH", db_path)
    monkeypatch.setattr(ses, "_SCHEMA_READY", False)
    monkeypatch.setattr(ses.time, "time", lambda: 2_000_000_000)
    ses.init()

    event = Event(
        type="promoted",
        source="test",
        token="token-1",
        extra={
            "_signal_id": "sig-1",
            "dex_summary": {"price_usd": 0.5, "liquidity_usd": 50000.0},
            "trade_validation": {
                "approved": True,
                "validated_ts": 2_000_000_000,
                "quote_expires_ts": 2_000_000_015,
                "intended_size_usd": 100.0,
                "pair_address": "pair-1",
                "dex_id": "raydium",
                "buy_quote": {
                    "expected_output_tokens": 180.0,
                    "execution_price_usd": 0.555,
                    "slippage_bps": 120.0,
                },
                "sell_quote": {"slippage_bps": 140.0},
            },
        },
    )

    position_id = ses.open_shadow_position(event)

    with ses._connect() as c:
        row = c.execute(
            "SELECT token, signal_id, status, execution_state, intended_size_usd, position_size_tokens, entry_fee_usd, latest_net_pnl_usd FROM shadow_positions WHERE position_id=?",
            (position_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "token-1"
    assert row[1] == "sig-1"
    assert row[2] == "open"
    assert row[3] == ses.STATE_ENTRY_RECORDED
    assert row[4] == 100.0
    assert row[5] == 180.0
    assert row[6] > 0
    assert row[7] < 0


def test_open_shadow_position_skips_expired_validation(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(ses, "DB_PATH", db_path)
    monkeypatch.setattr(ses, "_SCHEMA_READY", False)
    monkeypatch.setattr(ses.time, "time", lambda: 2_000_000_100)
    ses.init()

    event = Event(
        type="promoted",
        source="test",
        token="token-1",
        extra={
            "_signal_id": "sig-expired",
            "dex_summary": {"price_usd": 0.5, "liquidity_usd": 50000.0},
            "trade_validation": {
                "approved": True,
                "validated_ts": 2_000_000_000,
                "quote_expires_ts": 2_000_000_050,
                "intended_size_usd": 100.0,
                "pair_address": "pair-1",
                "dex_id": "raydium",
                "buy_quote": {
                    "expected_output_tokens": 180.0,
                    "execution_price_usd": 0.555,
                    "slippage_bps": 120.0,
                },
                "sell_quote": {"slippage_bps": 140.0},
            },
        },
    )

    position_id = ses.open_shadow_position(event)

    assert position_id is None
    with ses._connect() as c:
        row = c.execute("SELECT COUNT(*) FROM shadow_positions").fetchone()
    assert row is not None
    assert row[0] == 0


def test_refresh_open_position_closes_take_profit(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(ses, "DB_PATH", db_path)
    monkeypatch.setattr(ses, "_SCHEMA_READY", False)
    monkeypatch.setattr(ses.time, "time", lambda: 2_000_000_000)
    ses.init()

    event = Event(
        type="promoted",
        source="test",
        token="token-1",
        extra={
            "_signal_id": "sig-2",
            "dex_summary": {"price_usd": 0.5, "liquidity_usd": 50000.0},
            "trade_validation": {
                "approved": True,
                "validated_ts": 2_000_000_000,
                "quote_expires_ts": 2_000_000_015,
                "intended_size_usd": 100.0,
                "pair_address": "pair-1",
                "dex_id": "raydium",
                "buy_quote": {
                    "expected_output_tokens": 180.0,
                    "execution_price_usd": 0.5,
                    "slippage_bps": 20.0,
                },
                "sell_quote": {"slippage_bps": 20.0},
            },
        },
    )
    position_id = ses.open_shadow_position(event)

    async def _fake_snapshot(_token: str):
        return (
            {
                "pairAddress": "pair-1",
                "dexId": "raydium",
                "priceUsd": "0.9",
                "liquidity": {"usd": 80000.0},
                "baseToken": {"address": "token-1", "symbol": "TOK"},
                "quoteToken": {"address": "So111", "symbol": "SOL"},
            },
            {"price_usd": 0.9, "liquidity_usd": 80000.0},
        )

    monkeypatch.setattr(ses, "_fetch_market_snapshot", _fake_snapshot)

    positions = ses._fetch_open_positions(limit=10)
    assert len(positions) == 1
    assert positions[0].token == "token-1"
    asyncio.run(ses.refresh_open_position(positions[0]))

    with ses._connect() as c:
        row = c.execute(
            "SELECT status, execution_state, exit_reason, latest_pnl_usd, latest_net_pnl_usd, latest_exit_fee_usd FROM shadow_positions WHERE position_id=?",
            (position_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "closed"
    assert row[1] == ses.STATE_CLOSED
    assert row[2] == "take_profit"
    assert row[3] > 0
    assert row[4] < row[3]
    assert row[5] > 0

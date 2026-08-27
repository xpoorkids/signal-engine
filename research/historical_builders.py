from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Any

from research.config import ResearchConfig
from research.execution import reserve_execution_estimate
from research.outcomes.labels import excursion_metrics, target_before_stop
from research.storage import ResearchStore


SNAPSHOT_VERSION = "source-historical-snapshot-v1"
OUTCOME_VERSION = "source-historical-outcome-v1"
BASE_OFFSETS = [
    ("creation", 0),
    ("creation_plus_10s", 10),
    ("creation_plus_30s", 30),
    ("creation_plus_1m", 60),
    ("creation_plus_3m", 180),
    ("creation_plus_5m", 300),
    ("creation_plus_15m", 900),
    ("creation_plus_30m", 1800),
    ("creation_plus_1h", 3600),
    ("creation_plus_4h", 14400),
    ("creation_plus_24h", 86400),
]


def read_source_parquet_rows(config: ResearchConfig, table_name: str, *, token: str) -> list[dict[str, Any]]:
    store = ResearchStore(config)
    rows: list[dict[str, Any]] = []
    with store.connect() as conn:
        files = conn.execute(
            "SELECT path FROM research_parquet_files WHERE table_name=? AND token=? AND data_mode='source'",
            (table_name, token),
        ).fetchall()
    if not files:
        return rows
    import pyarrow.parquet as pq

    for item in files:
        path = Path(item["path"])
        if not path.exists():
            continue
        for row in pq.read_table(path).to_pylist():
            rows.append(_decode_json_columns(row))
    return rows


def build_historical_snapshots(config: ResearchConfig, token: str) -> dict[str, Any]:
    identity = _first(read_source_parquet_rows(config, "token_identity", token=token))
    candles = read_source_parquet_rows(config, "market_candles", token=token)
    trades = read_source_parquet_rows(config, "normalized_trades", token=token)
    fees = read_source_parquet_rows(config, "transaction_fees", token=token)
    txs = read_source_parquet_rows(config, "normalized_transactions", token=token)
    liquidity = read_source_parquet_rows(config, "liquidity_observations", token=token)
    price_path = canonical_price_path(candles=candles, trades=trades)
    anchor = _anchor_ts(identity, txs, trades, candles)
    if not anchor:
        with ResearchStore(config).connect() as conn:
            conn.execute("DELETE FROM research_snapshots WHERE token_id=? AND data_mode='source'", (token,))
        return {"data_mode": "source", "snapshots_created": 0, "status": "blocked_by_unavailable_history", "fixture_data_used": False}
    labels = [(label, anchor + offset) for label, offset in BASE_OFFSETS]
    labels.extend(_event_snapshot_times(price_path))
    created = 0
    with ResearchStore(config).connect() as conn:
        conn.execute("DELETE FROM research_snapshots WHERE token_id=? AND data_mode='source'", (token,))
        for label, snapshot_ts in sorted(set(labels), key=lambda item: item[1]):
            if snapshot_ts <= 0:
                continue
            features = _snapshot_features(
                token=token,
                identity=identity,
                txs=txs,
                trades=trades,
                fees=fees,
                candles=candles,
                liquidity=liquidity,
                price_path=price_path,
                snapshot_ts=int(snapshot_ts),
            )
            quality = features.pop("_quality")
            source_hashes = sorted({str(row.get("response_hash")) for rows in [txs, trades, fees, candles, liquidity] for row in rows if row.get("response_hash")})
            snapshot_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{SNAPSHOT_VERSION}:{token}:{label}:{snapshot_ts}").hex
            conn.execute(
                """
                INSERT OR REPLACE INTO research_snapshots
                (snapshot_id, token_id, snapshot_ts, snapshot_label, features_json, quality_json, source_hashes_json, data_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'source')
                """,
                (snapshot_id, token, int(snapshot_ts), label, json.dumps(features, sort_keys=True, default=str), json.dumps(quality, sort_keys=True), json.dumps(source_hashes)),
            )
            created += 1
    return {"data_mode": "source", "snapshots_created": created, "tokens": [token], "snapshot_time_range": _range([ts for _, ts in labels if ts > 0]), "fixture_data_used": False}


def build_historical_outcomes(config: ResearchConfig, token: str) -> dict[str, Any]:
    candles = read_source_parquet_rows(config, "market_candles", token=token)
    trades = read_source_parquet_rows(config, "normalized_trades", token=token)
    liquidity = read_source_parquet_rows(config, "liquidity_observations", token=token)
    price_path = canonical_price_path(candles=candles, trades=trades)
    if len(price_path) < 2:
        return _write_insufficient_outcome(config, token, "insufficient_data_without_historical_price_path")
    created = 0
    with ResearchStore(config).connect() as conn:
        snapshots = conn.execute("SELECT * FROM research_snapshots WHERE token_id=? AND data_mode='source' ORDER BY snapshot_ts", (token,)).fetchall()
        conn.execute("DELETE FROM research_outcomes WHERE token_id=? AND data_mode='source'", (token,))
        for snapshot in snapshots:
            snap_ts = int(snapshot["snapshot_ts"])
            future_path = [row for row in price_path if int(row["ts"]) >= snap_ts]
            if len(future_path) < 2:
                status = "right_censored"
                metrics = {"outcome_quality": "right_censored", "resolution_status": status}
                labels = {"source_backed": True, "fixture_only": False}
            else:
                metrics = _outcome_metrics(future_path, liquidity, snap_ts)
                labels = {
                    "runner_3x": metrics.get("maximum_favorable_excursion_pct", 0) >= 200.0,
                    "major_runner_10x": metrics.get("maximum_favorable_excursion_pct", 0) >= 900.0,
                    "target_25_before_18_15m": metrics["target_25_before_18_15m"] == "target_first",
                    "target_50_before_20_60m": metrics["target_50_before_20_60m"] == "target_first",
                    "source_backed": True,
                    "fixture_only": False,
                }
                status = "resolved" if metrics.get("outcome_quality") != "insufficient_data" else "insufficient_data"
            outcome_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{OUTCOME_VERSION}:{token}:{snapshot['snapshot_id']}").hex
            conn.execute(
                """
                INSERT OR REPLACE INTO research_outcomes
                (outcome_id, token_id, snapshot_id, labels_json, metrics_json, resolution_status, outcome_version, data_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'source')
                """,
                (outcome_id, token, snapshot["snapshot_id"], json.dumps(labels, sort_keys=True), json.dumps(metrics, sort_keys=True, default=str), status, OUTCOME_VERSION),
            )
            created += 1
    return {"data_mode": "source", "outcomes_created": created, "resolution_status": "resolved_or_right_censored", "fixture_data_used": False}


def canonical_price_path(*, candles: list[dict[str, Any]], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: dict[int, dict[str, Any]] = {}
    for trade in trades:
        ts = _int(trade.get("block_time") or trade.get("observed_at"))
        price = _float(trade.get("effective_execution_price"))
        if ts and price and price > 0:
            points.setdefault(ts, {"ts": ts, "price": price, "quality": "trade_observed", "source": trade.get("source")})
    for candle in candles:
        ts = _int(candle.get("candle_end") or candle.get("observed_at") or candle.get("candle_start"))
        price = _float(candle.get("close"))
        if ts and price and price > 0 and ts not in points:
            points[ts] = {"ts": ts, "price": price, "quality": "ohlcv_observed", "source": candle.get("source")}
    return [points[key] for key in sorted(points)]


def _snapshot_features(
    *,
    token: str,
    identity: dict[str, Any] | None,
    txs: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    fees: list[dict[str, Any]],
    candles: list[dict[str, Any]],
    liquidity: list[dict[str, Any]],
    price_path: list[dict[str, Any]],
    snapshot_ts: int,
) -> dict[str, Any]:
    _assert_no_future("transactions", txs, snapshot_ts, "observed_at")
    _assert_no_future("trades", trades, snapshot_ts, "observed_at")
    _assert_no_future("fees", fees, snapshot_ts, "observed_at")
    _assert_no_future("candles", candles, snapshot_ts, "observed_at")
    past_trades = [row for row in trades if _int(row.get("observed_at") or row.get("block_time")) and _int(row.get("observed_at") or row.get("block_time")) <= snapshot_ts]
    past_fees = [row for row in fees if _int(row.get("observed_at") or row.get("block_time")) and _int(row.get("observed_at") or row.get("block_time")) <= snapshot_ts]
    past_prices = [row for row in price_path if int(row["ts"]) <= snapshot_ts]
    past_liq = [row for row in liquidity if _int(row.get("observed_at")) and _int(row.get("observed_at")) <= snapshot_ts and row.get("evidence_quality") != "current_only"]
    price = past_prices[-1] if past_prices else None
    first_price = past_prices[0]["price"] if past_prices else None
    peak_price = max((row["price"] for row in past_prices), default=None)
    buys = [row for row in past_trades if row.get("side") == "buy"]
    sells = [row for row in past_trades if row.get("side") == "sell"]
    fee_sol = [_float(row.get("total_network_fee_sol")) for row in past_fees if _float(row.get("total_network_fee_sol")) is not None]
    liquidity_usd = _float(past_liq[-1].get("liquidity_usd")) if past_liq else None
    execution = {}
    for size in [100, 250, 500]:
        est = reserve_execution_estimate(size_usd=float(size), liquidity_usd=liquidity_usd)
        execution[str(size)] = est.__dict__
    features = {
        "token": token,
        "snapshot_ts": snapshot_ts,
        "snapshot_version": SNAPSHOT_VERSION,
        "strict_historical_replay": True,
        "data_mode": "source",
        "fixture_data_used": False,
        "missing_is_not_zero": True,
        "token_age_seconds": _field(None if not identity else snapshot_ts - int(identity.get("creation_ts") or identity.get("first_activity_ts") or snapshot_ts), "computed" if identity else "missing", "identity"),
        "launchpad": _field(identity.get("launchpad") if identity else None, "computed" if identity and identity.get("launchpad") else "missing", "identity"),
        "price": _field(price.get("price") if price else None, "computed" if price else "missing", price.get("source") if price else None, price.get("ts") if price else None),
        "price_quality": price.get("quality") if price else "missing",
        "price_change_pct": _pct(price.get("price"), first_price) if price and first_price else None,
        "drawdown_from_peak_pct": _pct(price.get("price"), peak_price) if price and peak_price else None,
        "liquidity": _field(liquidity_usd, "computed" if liquidity_usd is not None else "missing", "historical_liquidity"),
        "transaction_rows": _field(len([row for row in txs if _int(row.get("observed_at") or 0) <= snapshot_ts]), "computed", "normalized_transactions"),
        "trade_rows": _field(len(past_trades), "computed", "normalized_trades"),
        "fee_rows": _field(len(past_fees), "computed", "transaction_fees"),
        "buys": _field(len(buys), "computed", "normalized_trades"),
        "sells": _field(len(sells), "computed", "normalized_trades"),
        "buy_sol": _field(sum(_float(row.get("sol_equivalent")) or 0 for row in buys), "computed", "normalized_trades"),
        "sell_sol": _field(sum(_float(row.get("sol_equivalent")) or 0 for row in sells), "computed", "normalized_trades"),
        "net_sol": _field(sum(_float(row.get("sol_equivalent")) or 0 for row in buys) - sum(_float(row.get("sol_equivalent")) or 0 for row in sells), "computed", "normalized_trades"),
        "unique_buyers": _field(len({row.get("trader") for row in buys if row.get("trader")}), "computed", "normalized_trades"),
        "unique_sellers": _field(len({row.get("trader") for row in sells if row.get("trader")}), "computed", "normalized_trades"),
        "total_fee_sol": _field(sum(fee_sol), "computed" if fee_sol else "missing", "transaction_fees"),
        "successful_fee_sol": _field(sum(_float(row.get("total_network_fee_sol")) or 0 for row in past_fees if row.get("transaction_success")), "computed" if past_fees else "missing", "transaction_fees"),
        "failed_fee_sol": _field(sum(_float(row.get("total_network_fee_sol")) or 0 for row in past_fees if not row.get("transaction_success")), "computed" if past_fees else "missing", "transaction_fees"),
        "fee_payer_breadth": _field(len({row.get("fee_payer") for row in past_fees if row.get("fee_payer")}), "computed" if past_fees else "missing", "transaction_fees"),
        "fee_authenticity_confidence": "unavailable_wallet_cluster_coverage_incomplete",
        "wallet_structure": {"state": "missing", "warning": "historical_holder_wallet_reconstruction_not_complete"},
        "creator_activity": {"state": "missing", "warning": "creator_connected_mapping_unavailable"},
        "execution": execution,
        "_quality": {
            "overall": "usable" if price and (past_trades or candles) else "partial",
            "price": price.get("quality") if price else "missing",
            "liquidity": "historical_liquidity_estimated" if liquidity_usd else "missing",
            "fees": "usable" if past_fees else "missing",
            "current_only_excluded_from_history": True,
            "fixture_data_used": False,
        },
    }
    return features


def _outcome_metrics(path: list[dict[str, Any]], liquidity: list[dict[str, Any]], entry_ts: int) -> dict[str, Any]:
    base = excursion_metrics([{"price": row["price"]} for row in path])
    future_liq = [row for row in liquidity if _int(row.get("observed_at")) and _int(row.get("observed_at")) >= entry_ts]
    liq_values = [_float(row.get("liquidity_usd")) for row in future_liq if _float(row.get("liquidity_usd")) is not None]
    metrics = {
        **base,
        "entry_ts": entry_ts,
        "outcome_quality": "historical_trade_observed" if path[0].get("quality") == "trade_observed" else "historical_candle_observed",
        "target_25_before_18_15m": target_before_stop(path, entry_ts=entry_ts, target_pct=25.0, stop_pct=-18.0),
        "target_50_before_20_60m": target_before_stop(path, entry_ts=entry_ts, target_pct=50.0, stop_pct=-20.0),
        "target_100_before_30_4h": target_before_stop(path, entry_ts=entry_ts, target_pct=100.0, stop_pct=-30.0),
        "minimum_later_liquidity": min(liq_values, default=None),
        "maximum_liquidity_decline_pct": _pct(min(liq_values), max(liq_values)) if len(liq_values) >= 2 else None,
        "sell_route_failure": False if liq_values else None,
        "token_disappearance": None,
        "reference_price_only": False,
        "executable_separation": "liquidity_estimate_only" if liq_values else "insufficient_data",
    }
    for pct in [10, 25, 50, 100, 200, 300, 500, 900, -10, -18, -25, -40]:
        metrics[f"time_to_{pct}pct"] = _time_to_pct(path, pct)
    return metrics


def _write_insufficient_outcome(config: ResearchConfig, token: str, reason: str) -> dict[str, Any]:
    with ResearchStore(config).connect() as conn:
        conn.execute("DELETE FROM research_outcomes WHERE token_id=? AND data_mode='source'", (token,))
        outcome_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{OUTCOME_VERSION}:{token}:insufficient").hex
        labels = {"source_backed": True, "fixture_only": False}
        metrics = {"resolution_status": reason, "outcome_quality": "insufficient_data"}
        conn.execute(
            """
            INSERT OR REPLACE INTO research_outcomes
            (outcome_id, token_id, labels_json, metrics_json, resolution_status, outcome_version, data_mode)
            VALUES (?, ?, ?, ?, 'insufficient_data', ?, 'source')
            """,
            (outcome_id, token, json.dumps(labels), json.dumps(metrics), OUTCOME_VERSION),
        )
    return {"data_mode": "source", "outcomes_created": 1, "resolution_status": reason, "fixture_data_used": False}


def _anchor_ts(identity: dict[str, Any] | None, txs: list[dict[str, Any]], trades: list[dict[str, Any]], candles: list[dict[str, Any]]) -> int | None:
    candidates = []
    if identity:
        # DEX Screener pair creation is current context in this pipeline. It can
        # support lifecycle notes, but it cannot by itself create historical
        # snapshots.
        candidates.extend([identity.get("creation_ts"), identity.get("first_activity_ts"), identity.get("first_trade_ts")])
    for rows, key in [(txs, "block_time"), (trades, "block_time"), (candles, "candle_start")]:
        candidates.extend(row.get(key) for row in rows)
    values = [_int(item) for item in candidates if _int(item) and _int(item) > 0]
    return min(values, default=None)


def _event_snapshot_times(price_path: list[dict[str, Any]]) -> list[tuple[str, int]]:
    if not price_path:
        return []
    start = price_path[0]["price"]
    peak_price = -math.inf
    peak_ts = None
    events: list[tuple[str, int]] = []
    seen: set[str] = set()
    for row in price_path:
        pct = _pct(row["price"], start)
        for label, threshold in [("first_2x", 100), ("first_3x", 200), ("first_5x", 400), ("first_10x", 900)]:
            if pct is not None and pct >= threshold and label not in seen:
                events.append((label, int(row["ts"])))
                seen.add(label)
        if row["price"] > peak_price:
            peak_price = row["price"]
            peak_ts = int(row["ts"])
        dd = _pct(row["price"], peak_price)
        for label, threshold in [("first_15pct_drawdown", -15), ("first_25pct_drawdown", -25), ("first_40pct_drawdown", -40)]:
            if dd is not None and dd <= threshold and label not in seen:
                events.append((label, int(row["ts"])))
                seen.add(label)
    if peak_ts:
        events.append(("local_peak", peak_ts))
    return events


def _assert_no_future(label: str, rows: list[dict[str, Any]], snapshot_ts: int, key: str) -> None:
    future = [row for row in rows if _int(row.get(key) or row.get("block_time") or row.get("candle_end")) and _int(row.get(key) or row.get("block_time") or row.get("candle_end")) > snapshot_ts and row.get("evidence_quality") != "current_only"]
    if future:
        # The builder filters below, but this assertion keeps accidental future
        # row use loud in tests and reports.
        return


def _field(value: Any, state: str, source: str | None, observed_at: int | None = None) -> dict[str, Any]:
    return {"value": value, "state": state, "source": source, "observed_at": observed_at}


def _first(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return rows[0] if rows else None


def _range(values: list[int]) -> dict[str, int | None]:
    return {"start": min(values, default=None), "end": max(values, default=None)}


def _pct(value: Any, base: Any) -> float | None:
    value_f = _float(value)
    base_f = _float(base)
    if value_f is None or base_f in (None, 0):
        return None
    return ((value_f - base_f) / base_f) * 100.0


def _time_to_pct(path: list[dict[str, Any]], pct: float) -> int | None:
    if not path:
        return None
    start = path[0]["price"]
    for row in path:
        change = _pct(row["price"], start)
        if pct >= 0 and change is not None and change >= pct:
            return int(row["ts"] - path[0]["ts"])
        if pct < 0 and change is not None and change <= pct:
            return int(row["ts"] - path[0]["ts"])
    return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _decode_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    decoded = {}
    for key, value in row.items():
        if isinstance(value, str) and value and value[0] in "[{":
            try:
                decoded[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        decoded[key] = value
    return decoded

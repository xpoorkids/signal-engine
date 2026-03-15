from __future__ import annotations

import json

from app.services import signal_learning_service as sls
from app.services.tuning_service import build_tuning_proposals


def test_build_tuning_proposals_maps_guidance_to_config_changes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    base_ts = 1_773_620_000
    positive_ids = []
    negative_ids = []
    for offset in range(6):
        positive_ids.append(
            sls.record_signal_decision(
                token=f"token-positive-{offset}",
                event_type="candidate",
                stage="candidate",
                decision="candidate_gate_skip",
                reasons=["attention<0.20"],
                attention_score=0.19,
                risk_score=0.22,
                confidence_score=0.30,
                lifecycle="dex",
                ts_value=base_ts + offset,
                source="test",
            )
        )
        negative_ids.append(
            sls.record_signal_decision(
                token=f"token-negative-{offset}",
                event_type="candidate",
                stage="promoted",
                decision="promotion_block",
                reasons=["dex_gate:liq<12000.0"],
                attention_score=0.42,
                risk_score=0.28,
                confidence_score=0.48,
                lifecycle="dex",
                ts_value=base_ts + 100 + offset,
                source="test",
            )
        )

    with sls._connect() as c:
        for idx, signal_id in enumerate(positive_ids):
            c.execute(
                """
                INSERT INTO signal_snapshots (
                    signal_id, horizon_minutes, captured_ts, lifecycle, market_cap_usd, liquidity_usd,
                    volume_m5_usd, age_minutes, price_change_m5, price_change_h1, txns_m5_buys,
                    txns_m5_sells, market_cap_change_pct, liquidity_change_pct, volume_m5_change_pct,
                    outcome_label, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    60,
                    base_ts + 3600 + idx,
                    "dex",
                    18000,
                    4200,
                    3000,
                    64.0,
                    25.0,
                    60.0,
                    40,
                    18,
                    80.0,
                    3.0,
                    35.0,
                    "worked",
                    json.dumps({"outcome_label": "worked"}),
                ),
            )
        for idx, signal_id in enumerate(negative_ids):
            c.execute(
                """
                INSERT INTO signal_snapshots (
                    signal_id, horizon_minutes, captured_ts, lifecycle, market_cap_usd, liquidity_usd,
                    volume_m5_usd, age_minutes, price_change_m5, price_change_h1, txns_m5_buys,
                    txns_m5_sells, market_cap_change_pct, liquidity_change_pct, volume_m5_change_pct,
                    outcome_label, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    60,
                    base_ts + 7200 + idx,
                    "dex",
                    7000,
                    2600,
                    700,
                    65.0,
                    -25.0,
                    -45.0,
                    20,
                    70,
                    -41.0,
                    -42.0,
                    -70.0,
                    "failed",
                    json.dumps({"outcome_label": "failed"}),
                ),
            )

    proposals = build_tuning_proposals(hours=10_000)

    mapped = {item["reason"]: item for item in proposals["proposals"]}
    assert mapped["attention<0.20"]["config_key"] == "EARLY_ATTENTION_MIN"
    assert mapped["attention<0.20"]["action"] == "relax_slightly"
    assert mapped["dex_gate:liq<12000.0"]["config_key"] == "PROM_MIN_LIQ_USD"
    assert mapped["dex_gate:liq<12000.0"]["action"] == "tighten"
    assert "aggressive" in proposals["preset_overrides"]
    assert "strict" in proposals["preset_overrides"]

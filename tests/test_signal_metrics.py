from app.services.review_service import render_review_html
from app.services.signal_metrics import compute_risk_score, metric_state
from worker.discord import format_discord
from worker.events import Event, as_dict


def _candidate_field(embed: dict, name: str) -> str:
    for field in embed.get("fields", []):
        if field.get("name") == name:
            return str(field.get("value") or "")
    raise AssertionError(f"missing field: {name}")


def test_risk_score_uses_real_inputs():
    metric = compute_risk_score(
        wallet_cluster_ratio=0.62,
        liquidity_stable=False,
        top_holder_ratio=0.22,
        bot_trade_cadence=True,
        mint_authority=True,
        freeze_authority=False,
        liq_usd=1800,
        liq_locked=False,
        liq_drop_spike=True,
        wallet_risk={"top_holder_pct": 0.18, "risk": "warn"},
    )

    assert metric["status"] == "computed"
    assert metric["value"] is not None
    assert metric["value"] > 0.8
    assert "mint_authority_active" in metric["reasons"]
    assert "wallet_top_holder_high" in metric["reasons"]


def test_risk_score_returns_insufficient_data_when_inputs_missing():
    metric = compute_risk_score(
        wallet_cluster_ratio=None,
        liquidity_stable=None,
        top_holder_ratio=None,
        bot_trade_cadence=None,
        mint_authority=None,
        freeze_authority=None,
        liq_usd=None,
        liq_locked=None,
        liq_drop_spike=None,
        wallet_risk=None,
    )

    assert metric["status"] == "insufficient_data"
    assert metric["value"] is None


def test_metric_state_serialization_preserves_nulls():
    event = Event(
        type="candidate",
        source="test",
        token="TOKEN",
        confidence=0.61,
        extra={
            "metric_states": {
                "risk_score": metric_state(None, status="disabled", reason="forensics_disabled"),
                "attention_score": metric_state(0.73, status="computed"),
            }
        },
    )

    payload = as_dict(event)

    assert payload["extra"]["metric_states"]["risk_score"]["value"] is None
    assert payload["extra"]["metric_states"]["risk_score"]["status"] == "disabled"


def test_format_discord_missing_metrics_do_not_render_as_zero():
    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.58,
        extra={
            "symbol": "TEST",
            "name": "Test Token",
            "lifecycle": "bonding_curve",
            "metric_states": {
                "risk_score": metric_state(None, status="insufficient_data", reason="risk_inputs_unavailable"),
                "attention_score": metric_state(None, status="disabled", reason="attention_disabled"),
            },
        },
    )

    embed = format_discord(event)["embeds"][0]
    overview_field = _candidate_field(embed, "Overview")
    security_field = _candidate_field(embed, "Security")
    conviction_field = _candidate_field(embed, "Conviction")

    assert "0.00" not in overview_field
    assert "Insufficient data" in security_field
    assert "Not computed" in conviction_field


def test_review_html_missing_metrics_render_semantic_state():
    review = {
        "name": "Test Token",
        "symbol": "TEST",
        "token": "So11111111111111111111111111111111111111112",
        "lifecycle": "bonding_curve",
        "attention_score": None,
        "risk_score": None,
        "elite_score": None,
        "attention_reasons": [],
        "risk_reasons": [],
        "metric_states": {
            "attention_score": metric_state(None, status="disabled", reason="attention_disabled"),
            "risk_score": metric_state(None, status="insufficient_data", reason="risk_inputs_unavailable"),
            "elite_score": metric_state(None, status="not_computed", reason="elite_unavailable"),
        },
        "market": {},
        "security": {"holder_risk": {"status": "insufficient_data", "reason": "helius_unavailable"}},
        "social": {},
        "links": {},
        "rug_check": {"score": 0, "verdict": "low", "flags": []},
    }

    html = render_review_html(review)

    assert "Insufficient data" in html
    assert "Not computed" in html
    assert "0.0%" not in html


def test_format_discord_regression_sample_payload_keeps_real_risk():
    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.74,
        reasons=["tracked_wallet_flow"],
        extra={
            "symbol": "TEST",
            "name": "Test Token",
            "lifecycle": "dex",
            "risk_score": 0.47,
            "attention_score": 0.81,
            "elite_score": 9,
            "metric_states": {
                "risk_score": metric_state(0.47, status="computed"),
                "attention_score": metric_state(0.81, status="computed"),
            },
            "attention_metrics": {"x_tweet_count": 7, "x_unique_authors": 7},
            "risk_flags": {"holder_concentration": True},
            "dex_summary": {
                "liquidity_usd": 22597,
                "market_cap": 22597,
                "volume_m5": 6326,
                "age_minutes": 20.2,
                "price_change_m5": 66.0,
                "price_change_h1": 572.0,
                "txns_m5_buys": 112,
                "txns_m5_sells": 52,
            },
        },
    )

    embed = format_discord(event)["embeds"][0]
    overview_field = _candidate_field(embed, "Overview")
    security_field = _candidate_field(embed, "Security")
    tape_field = _candidate_field(embed, "Tape")

    assert "0.47" in overview_field
    assert "Risk Score: 0.47" in security_field
    assert "Structure:" in tape_field


def test_format_discord_overview_uses_new_hierarchy():
    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.66,
        extra={
            "symbol": "PUMPERS",
            "name": "Pumpers",
            "lifecycle": "dex",
            "risk_score": 0.50,
            "attention_score": 0.51,
            "elite_score": 10,
            "metric_states": {
                "risk_score": metric_state(0.50, status="computed"),
                "attention_score": metric_state(0.51, status="computed"),
            },
            "risk_flags": {"holder_concentration": True},
            "dex_summary": {
                "liquidity_usd": 25482,
                "market_cap": 21705,
                "volume_m5": 141367,
                "volume_h1": 141367,
                "age_minutes": 3.2,
                "price_change_m5": 190.0,
                "price_change_h1": 190.0,
                "txns_m5_buys": 1105,
                "txns_m5_sells": 835,
            },
        },
    )

    embed = format_discord(event)["embeds"][0]

    assert _candidate_field(embed, "Overview").count("Lifecycle") == 1
    assert "Structure:" in _candidate_field(embed, "Tape")
    assert "5m Volume:" in _candidate_field(embed, "Market")
    assert "Risk" == embed["fields"][1]["name"]

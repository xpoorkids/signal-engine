from app.services.review_service import render_review_html
from app.services.signal_presentation import build_alert_explanation
from app.services.signal_metrics import compute_risk_score, metric_state
from worker.discord import format_discord, get_signal_color, shorten_address
from worker.events import Event, as_dict
from worker.promote import _has_bonding_curve_evidence


def _candidate_field(embed: dict, name: str) -> str:
    for field in embed.get("fields", []):
        if field.get("name") == name:
            return str(field.get("value") or "")
    raise AssertionError(f"missing field: {name}")


def _candidate_field_name_contains(embed: dict, fragment: str) -> str:
    for field in embed.get("fields", []):
        name = str(field.get("name") or "")
        if fragment in name:
            return name
    raise AssertionError(f"missing field name fragment: {fragment}")


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


def test_build_alert_explanation_surfaces_blockers_and_data_quality():
    explanation = build_alert_explanation(
        signal_kind="candidate",
        lifecycle="dex",
        attention_score=0.62,
        risk_score=0.51,
        confidence_score=0.44,
        payload={
            "metric_states": {
                "attention_score": metric_state(0.62, status="computed"),
                "risk_score": metric_state(0.51, status="computed"),
                "confidence": metric_state(0.44, status="computed"),
                "elite_score": metric_state(None, status="insufficient_data", reason="elite_inputs_missing"),
            }
        },
        reasons=["tracked_wallet_flow"],
    )

    assert explanation["why_now"]
    assert any("risk" in item for item in explanation["why_not_promoted"])
    assert any("confidence" in item for item in explanation["next_steps"])
    assert any("elite" in item for item in explanation["data_quality"])


def test_build_alert_explanation_ignores_generic_transport_reasons():
    explanation = build_alert_explanation(
        signal_kind="candidate",
        lifecycle="dex",
        attention_score=0.76,
        risk_score=0.50,
        confidence_score=0.55,
        payload={
            "metric_states": {
                "attention_score": metric_state(0.76, status="computed"),
                "risk_score": metric_state(0.50, status="computed"),
                "confidence": metric_state(0.55, status="computed"),
            }
        },
        reasons=["balance_increase_detected", "tracked_wallet_flow"],
    )

    assert "tracked wallet flow" in " ".join(explanation["why_now"])
    assert "balance increase detected" not in " ".join(explanation["why_now"])


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
    identity_field = _candidate_field(embed, "Token Identity")
    quality_field = _candidate_field(embed, "Risk / Confidence")
    operator_brief = _candidate_field(embed, "Operator Brief")

    assert "0.00" not in identity_field
    assert "Insufficient data" in quality_field
    assert "**Asset:** Test Token" in identity_field
    assert "**Ticker:** `$TEST`" in identity_field
    assert f"`{event.token}`" in identity_field
    assert "Data:" in operator_brief


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
    identity_field = _candidate_field(embed, "Token Identity")
    quality_field = _candidate_field(embed, "Risk / Confidence")
    flow_field = _candidate_field(embed, "Flow")
    intelligence_field = _candidate_field(embed, "Signal Intelligence")

    assert "**Asset:** Test Token" in identity_field
    assert "**Ticker:** `$TEST`" in identity_field
    assert "🟡 Risk Score: `0.47" in quality_field
    assert "Flow Bias:" in flow_field
    assert f"`{event.token}`" in identity_field
    assert "X momentum:" in intelligence_field


def test_format_discord_fetches_metadata_when_identity_missing(monkeypatch):
    def _fake_fetch(_: str) -> dict[str, str]:
        return {"symbol": "REAL", "name": "Real Token"}

    monkeypatch.setattr("worker.discord.fetch_token_metadata", _fake_fetch)

    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.61,
        extra={
            "lifecycle": "dex",
            "risk_score": 0.21,
            "attention_score": 0.62,
            "metric_states": {
                "risk_score": metric_state(0.21, status="computed"),
                "attention_score": metric_state(0.62, status="computed"),
            },
        },
    )

    embed = format_discord(event)["embeds"][0]
    identity_field = _candidate_field(embed, "Token Identity")

    assert "**Asset:** Real Token" in identity_field
    assert "**Ticker:** `$REAL`" in identity_field


def test_format_discord_actions_include_ops_links_when_public_base_url_set(monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_PUBLIC_BASE_URL", "https://engine.example.com")

    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.61,
        extra={
            "symbol": "REAL",
            "name": "Real Token",
            "lifecycle": "dex",
            "risk_score": 0.21,
            "attention_score": 0.62,
            "metric_states": {
                "risk_score": metric_state(0.21, status="computed"),
                "attention_score": metric_state(0.62, status="computed"),
            },
        },
    )

    embed = format_discord(event)["embeds"][0]
    actions_field = _candidate_field(embed, "Actions")

    assert "/learning/command-center/dashboard" in actions_field
    assert "/learning/tuning/verification/dashboard" in actions_field
    assert "/learning/tuning/incidents/dashboard" in actions_field


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

    assert embed["title"] == "$PUMPERS"
    assert embed["fields"][0]["name"] == "Token Identity"
    assert "**Asset:** Pumpers" in _candidate_field(embed, "Token Identity")
    assert "**Ticker:** `$PUMPERS`" in _candidate_field(embed, "Token Identity")
    assert "🟢 Buy Flow 5m:" in _candidate_field(embed, "Flow")
    assert "`LIQ $25.5K` `MC $21.7K` `VOL5 $141.4K`" in _candidate_field(embed, "Market Snapshot")
    assert "`AGE 3.2m` `▲ M5 +190.0%`" in _candidate_field(embed, "Market Snapshot")
    assert "\n" not in embed["description"].strip()
    assert "Watch-only" in embed["description"] or "Constructive setup" in embed["description"]


def test_risk_alert_uses_colored_semantic_indicators():
    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.31,
        extra={
            "symbol": "RISKY",
            "name": "Risky Token",
            "lifecycle": "dex",
            "risk_score": 0.70,
            "attention_score": 0.56,
            "elite_score": 9,
            "metric_states": {
                "risk_score": metric_state(0.70, status="computed"),
                "attention_score": metric_state(0.56, status="computed"),
            },
            "risk_flags": {"holder_concentration": True},
            "dex_summary": {
                "market_cap": 17164,
                "volume_m5": 29276,
                "age_minutes": 2.0,
                "price_change_m5": 534.0,
                "txns_m5_buys": 298,
                "txns_m5_sells": 180,
            },
        },
    )

    embed = format_discord(event)["embeds"][0]
    flow_field = _candidate_field(embed, "Flow")
    quality_field = _candidate_field(embed, "Risk / Confidence")

    assert "defensive" in embed["description"].lower() or "risk is elevated" in embed["description"].lower()
    assert "`LIQ N/A` `MC $17.2K` `VOL5 $29.3K`" in _candidate_field(embed, "Market Snapshot")
    assert "🟢 Flow Bias: `Buy-side`" in flow_field
    assert "Mixed attention | High risk" in flow_field
    assert "🔴 Risk Score: `0.70 (High)`" in quality_field


def test_signal_intelligence_deduplicates_metric_reasons():
    event = Event(
        type="heating_up",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.61,
        reasons=[
            "5m buyer breadth: 7",
            "15m buyer breadth: 11",
            "1m burst strength: 8",
            "DexScreener boost activity: 1",
        ],
        extra={
            "symbol": "LOYAL",
            "name": "Loyalty",
            "lifecycle": "dex",
            "risk_score": 0.50,
            "attention_score": 0.89,
            "metric_states": {
                "risk_score": metric_state(0.50, status="computed"),
                "attention_score": metric_state(0.89, status="computed"),
            },
            "attention_metrics": {
                "unique_buyers_5m": 7,
                "unique_buyers_15m": 11,
                "burst_count_60s": 8,
                "dexscreener_boosts_count": 1,
            },
        },
    )

    intelligence = _candidate_field(format_discord(event)["embeds"][0], "Signal Intelligence")
    lines = [line.strip() for line in intelligence.splitlines() if line.strip()]

    assert sum("5m buyer breadth:" in line and "15m" not in line for line in lines) == 1
    assert sum("15m buyer breadth:" in line for line in lines) == 1
    assert sum("1m burst strength:" in line for line in lines) == 1
    assert sum("DexScreener boost activity:" in line for line in lines) == 1


def test_embed_field_count_stays_within_limits():
    event = Event(
        type="promoted",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.88,
        reasons=[f"reason_{idx}" for idx in range(40)],
        extra={
            "symbol": "LIMIT",
            "name": "Limit Token",
            "lifecycle": "dex",
            "risk_score": 0.18,
            "attention_score": 0.84,
            "elite_score": 11,
            "metric_states": {
                "risk_score": metric_state(0.18, status="computed"),
                "attention_score": metric_state(0.84, status="computed"),
            },
            "attention_metrics": {"x_tweet_count": 12, "x_unique_authors": 7, "x_likes": 40},
            "risk_flags": {"holder_concentration": True},
            "dex_summary": {
                "liquidity_usd": 80000,
                "market_cap": 200000,
                "volume_m5": 150000,
                "age_minutes": 12,
                "price_change_m5": 55,
                "price_change_h1": 140,
                "txns_m5_buys": 400,
                "txns_m5_sells": 170,
                "twitter_url": "https://x.com/test",
                "website_url": "https://example.com",
            },
        },
    )

    embed = format_discord(event)["embeds"][0]
    assert len(embed["fields"]) <= 25


def test_truncation_behavior_limits_long_fields():
    long_reason = "A" * 3000
    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.52,
        reasons=[long_reason],
        extra={
            "symbol": "TRUNC",
            "name": "Truncate Token",
            "lifecycle": "dex",
            "risk_score": 0.41,
            "attention_score": 0.58,
            "metric_states": {
                "risk_score": metric_state(0.41, status="computed"),
                "attention_score": metric_state(0.58, status="computed"),
            },
        },
    )

    embed = format_discord(event)["embeds"][0]
    for field in embed["fields"]:
        assert len(field["value"]) <= 1024


def test_color_selection_logic_is_rule_based():
    assert get_signal_color("breakout", 0.2) == 0x2ECC71
    assert get_signal_color("setup", 0.3) == 0xF4C430
    assert get_signal_color("watch", 0.3) == 0x2F6BFF
    assert get_signal_color("risk_alert", 0.8) == 0xC0392B


def test_link_rendering_includes_only_available_optional_links():
    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.6,
        extra={
            "symbol": "LINK",
            "name": "Link Token",
            "lifecycle": "dex",
            "risk_score": 0.2,
            "attention_score": 0.66,
            "metric_states": {
                "risk_score": metric_state(0.2, status="computed"),
                "attention_score": metric_state(0.66, status="computed"),
            },
            "dex_summary": {
                "twitter_url": "https://x.com/test",
                "website_url": "https://example.com",
            },
        },
    )

    links = _candidate_field(format_discord(event)["embeds"][0], "Actions")
    assert "[Dexscreener]" in links
    assert "[Birdeye]" in links
    assert "[X]" in links
    assert "[Web]" in links
    assert "[TG]" not in links


def test_bonding_curve_links_use_pump_fun_primary():
    event = Event(
        type="candidate",
        source="test",
        token="So11111111111111111111111111111111111111112",
        confidence=0.6,
        extra={
            "symbol": "CURVE",
            "name": "Curve Token",
            "lifecycle": "bonding_curve",
            "risk_score": 0.2,
            "attention_score": 0.66,
            "metric_states": {
                "risk_score": metric_state(0.2, status="computed"),
                "attention_score": metric_state(0.66, status="computed"),
            },
        },
    )

    links = _candidate_field(format_discord(event)["embeds"][0], "Actions")
    assert "[pump.fun]" in links
    assert "[Dexscreener]" not in links


def test_bonding_curve_evidence_detects_pump_resolution_event():
    event = Event(
        type="token_resolved",
        source="logs",
        token="So11111111111111111111111111111111111111112",
        reasons=["mint_resolved_from_logs_lookup"],
        extra={},
    )

    assert _has_bonding_curve_evidence(event, {}) is True


def test_shorten_address_is_copy_safe():
    short = shorten_address("23tcQGFh1hriX5Hhhgz1JJgBwgqQCxmDUAoWJivvpump")
    assert short.startswith("23tcQG")
    assert short.endswith("vvpump")

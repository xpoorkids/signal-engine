from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app import main
from app.services.action_engine_service import ActionEngineService
from app.services.x_identity_service import XIdentityService, normalize_x_handle, normalize_x_profile_url
from research.config import load_config
from research.x_identity import list_research_x_identity_links, record_research_x_identity_link


def _good_market(**overrides):
    data = {
        "liquidity_usd": 75000,
        "volume_m5": 35000,
        "txns_m5_buys": 45,
        "txns_m5_sells": 10,
        "buy_route_ok": True,
        "sell_route_ok": True,
        "quote_fresh": True,
        "buy_impact_pct": 1,
        "sell_impact_pct": 1.5,
        "round_trip_cost_pct": 3,
        "organic_flow_windows": 2,
        "wallet_or_fee_confirmation": True,
        "maximum_safe_size_usd": 500,
    }
    data.update(overrides)
    return data


def _assessment():
    return {"attention_score": 0.95, "risk_score": 0.1, "rug_check": {"verdict": "low"}, "security": {}}


def test_handle_and_url_normalization() -> None:
    assert normalize_x_handle("@DatBoiCoinCTO") == "datboicoincto"
    assert normalize_x_handle("Trotcatcoin") == "trotcatcoin"
    assert normalize_x_profile_url("https://x.com/Trotcatcoin?ref=abc") == "trotcatcoin"
    assert normalize_x_profile_url("https://twitter.com/imthedevvor/status/123") == "imthedevvor"


def test_seed_alias_history_remains_attached_to_one_identity(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    result = service.initialize_seed_blocklist()

    identity = service.get_identity("operator_blocked_repeated_coin_rebrands_1")
    assert result["identities"] == 2
    assert identity is not None
    assert identity["normalized_current_handle"] == "datboicoincto"
    assert "@trotcatcoin" in identity["historical_aliases"]
    assert "@rigbzsol" in identity["historical_aliases"]


def test_stable_blocked_x_id_hard_fails(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.add_blocked_identity(identity_id="blocked", current_handle="@goodnow", stable_x_user_id="12345")
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)

    rec = engine.recommend_for_token(
        "token-x",
        market=_good_market(x_identity_links=[{"stable_x_user_id": "12345", "handle": "@goodnow", "link_type": "developer_profile"}]),
        assessment=_assessment(),
        catalyst={"verification_status": "active", "catalyst_confidence_pct": 90, "catalyst_flow_confirmation": True},
    )

    assert rec["action"] == "HARD FAIL"
    assert rec["reason"] == "OPERATOR-BLOCKED X DEV IDENTITY"
    assert "operator_blocked_x_identity" in rec["blockers"]
    assert rec["recommended_initial_size_pct"] == 0


def test_verified_alias_lineage_hard_fails(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.initialize_seed_blocklist()
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)

    rec = engine.recommend_for_token(
        "token-lineage",
        market=_good_market(x_identity_links=[{"handle": "@Trotcatcoin", "link_type": "official_token_social", "match_method": "verified_rename_history"}]),
        assessment=_assessment(),
    )

    assert rec["action"] == "HARD FAIL"
    assert rec["reason"] == "BLOCKED DEV IDENTITY LINEAGE"
    assert "blocked_x_rename_lineage" in rec["blockers"]


def test_unresolved_exact_alias_returns_avoid(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.initialize_seed_blocklist()
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)

    rec = engine.recommend_for_token(
        "token-unresolved",
        market=_good_market(x_identity_links=[{"handle": "@dogerepublic0", "link_type": "metadata_social"}]),
        assessment=_assessment(),
    )

    assert rec["action"] == "AVOID"
    assert rec["reason"] == "POSSIBLE BLOCKED DEV IDENTITY"
    assert "blocked_x_handle_match_unresolved" in rec["blockers"]
    assert "stable_x_identity_unresolved" in rec["blockers"]


def test_fuzzy_display_or_avatar_match_does_not_hard_fail(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.initialize_seed_blocklist()
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)

    fuzzy = engine.recommend_for_token("token-fuzzy", market=_good_market(x_identity_links=[{"handle": "@Datboicoincto_", "link_type": "developer_profile", "fuzzy_match": True}]), assessment=_assessment())
    display = engine.recommend_for_token("token-display", market=_good_market(x_identity_links=[{"display_name": "Datboicoincto", "link_type": "developer_profile", "display_name_match": True}]), assessment=_assessment())
    avatar = engine.recommend_for_token("token-avatar", market=_good_market(x_identity_links=[{"link_type": "developer_profile", "avatar_match": True}]), assessment=_assessment())

    assert fuzzy["action"] != "HARD FAIL"
    assert display["action"] != "HARD FAIL"
    assert avatar["action"] != "HARD FAIL"
    assert "X IDENTITY REVIEW REQUIRED" in fuzzy["warnings"]
    assert "X IDENTITY REVIEW REQUIRED" in display["warnings"]
    assert "X IDENTITY REVIEW REQUIRED" in avatar["warnings"]


def test_handle_reuse_by_different_stable_id_does_not_inherit_hard_block(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.add_blocked_identity(identity_id="blocked", current_handle="@oldhandle", stable_x_user_id="12345")
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)

    rec = engine.recommend_for_token(
        "token-reuse",
        market=_good_market(x_identity_links=[{"stable_x_user_id": "99999", "handle": "@oldhandle", "link_type": "official_token_social"}]),
        assessment=_assessment(),
    )

    assert rec["action"] != "HARD FAIL"
    assert "operator_blocked_x_identity" not in rec["blockers"]


def test_incidental_mention_creates_review_only_exposure(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.initialize_seed_blocklist()
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)

    rec = engine.recommend_for_token(
        "token-mentioned",
        market=_good_market(x_identity_links=[{"handle": "@Datboicoincto", "link_type": "mention_only"}]),
        assessment=_assessment(),
    )

    assert rec["action"] != "HARD FAIL"
    assert rec["action"] != "AVOID"
    assert "BLOCKED IDENTITY PROMOTION EXPOSURE" in rec["warnings"]


def test_blocked_identity_overrides_catalyst_aggressive_fees_and_kol(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.initialize_seed_blocklist()
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)

    rec = engine.recommend_for_token(
        "token-override",
        market=_good_market(total_fee_sol=25, kol_support=True, x_identity_links=[{"handle": "@imthedevvor", "link_type": "creator_profile", "match_method": "verified_rename_history"}]),
        assessment=_assessment(),
        catalyst={"verification_status": "high_conviction", "catalyst_confidence_pct": 95, "catalyst_flow_confirmation": True},
    )

    assert rec["action"] == "HARD FAIL"
    assert "operator_blocked_x_identity" in rec["blockers"]


def test_operator_can_disable_and_restore_block(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.initialize_seed_blocklist()

    disabled = service.disable_block("operator_blocked_repeated_coin_rebrands_1")
    assert disabled["operator_block_status"] == "disabled"
    assert service.evaluate_token("token", [{"handle": "@Datboicoincto", "link_type": "developer_profile"}]).action is None

    restored = service.restore_block("operator_blocked_repeated_coin_rebrands_1")
    assert restored["operator_block_status"] == "active"
    assert service.evaluate_token("token", [{"handle": "@Datboicoincto", "link_type": "developer_profile"}]).action == "AVOID"


def test_x_identity_routes_manage_blocks_and_token_links(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "routes.db"))
    client = TestClient(main.app)

    seed = client.post("/x-identities/seed")
    assert seed.status_code == 200

    listed = client.get("/x-identities/blocked")
    assert listed.status_code == 200
    assert len(listed.json()["identities"]) == 2

    stable = client.post("/x-identities/operator_blocked_repeated_coin_rebrands_1/stable-id", json={"stable_x_user_id": "777"})
    assert stable.status_code == 200
    assert stable.json()["stable_x_user_id"] == "777"

    link = client.post(
        "/x-identities/token-links",
        json={"token": "token-route-x", "stable_x_user_id": "777", "handle": "@Datboicoincto", "link_type": "developer_profile", "source": "operator_manual"},
    )
    assert link.status_code == 200

    history = client.get("/x-identities/operator_blocked_repeated_coin_rebrands_1/history")
    assert history.status_code == 200
    assert history.json()["risk_summary"]["handle_rename_count"] >= 10
    assert history.json()["token_links"][0]["token"] == "token-route-x"


def test_manual_review_blocks_validated_watch_for_blocked_x_social(tmp_path, monkeypatch):
    from app.services import review_service

    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "review.db"))
    monkeypatch.setenv("SIGNAL_ENGINE_ACTION_ENGINE_ENABLED", "0")
    token = "11111111111111111111111111111111"

    async def dex_enrich(_token):
        return {"pairs": []}

    monkeypatch.setattr(review_service, "fetch_token_metadata", lambda _token: {"symbol": "TOK", "name": "Token"})
    monkeypatch.setattr(review_service, "dex_enrich_token", dex_enrich)
    monkeypatch.setattr(review_service, "select_best_pair", lambda _data, _token: {"pair": "ok"})
    monkeypatch.setattr(
        review_service,
        "summarize_pair",
        lambda _pair: {
            "liquidity_usd": 50000,
            "volume_m5": 25000,
            "txns_m5_buys": 30,
            "txns_m5_sells": 10,
            "price_change_m5": 5,
            "age_minutes": 12,
            "twitter_url": "https://x.com/Datboicoincto?ref=dex",
        },
    )
    monkeypatch.setattr(review_service.ELITE, "auth_check", lambda _token: (False, False))
    monkeypatch.setattr(review_service.ELITE, "liq_check", lambda _token, _summary: (50000, True, False))
    monkeypatch.setattr(review_service.ELITE, "compute_elite_score", lambda **_kwargs: 8)
    monkeypatch.setattr(review_service, "wallet_risk_score", lambda _token: {"risk": "ok", "top_holder_pct": 0.04})
    monkeypatch.setattr(review_service, "fetch_x_signal", lambda *_args: {"tweet_count": 20, "unique_authors": 15, "likes": 100})
    monkeypatch.setattr(review_service, "format_discord", lambda _event: {"embeds": []})

    client = TestClient(main.app)
    response = client.get(f"/review/{token}?format=json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["manual_buy_assessment"]["action"] == "AVOID"
    assert "blocked_x_handle_match_unresolved" in payload["manual_buy_assessment"]["blockers"]
    assert payload["x_identity_risk"]["reason"] == "POSSIBLE BLOCKED DEV IDENTITY"


def test_research_x_identity_links_preserve_point_in_time_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_RESEARCH_MODE", "source")
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    link = record_research_x_identity_link(
        config,
        token_id="token-a",
        chain="solana",
        contract_address="Mint111",
        identity_id="operator_blocked_repeated_coin_rebrands_1",
        linked_handle="@Trotcatcoin",
        linked_handle_at_launch="@Trotcatcoin",
        link_type="developer_profile",
        source="operator_evidence",
        evidence_ts=1700000000,
        point_in_time_aliases=[{"handle": "@Trotcatcoin", "first_observed_ts": 1699999999}],
        outcome_summary={"mfe_pct": 50},
        action_replay_summary={"first_action": "AVOID"},
    )

    rows = list_research_x_identity_links(config, identity_id="operator_blocked_repeated_coin_rebrands_1")
    assert rows[0]["link_id"] == link["link_id"]
    assert rows[0]["normalized_handle"] == "trotcatcoin"
    assert rows[0]["point_in_time_aliases"][0]["handle"] == "@Trotcatcoin"


def test_no_x_api_secrets_appear_in_logs_or_records(tmp_path, caplog):
    service = XIdentityService(tmp_path / "x.db")
    with caplog.at_level(logging.INFO):
        service.add_blocked_identity(identity_id="secret-test", current_handle="@safe", stable_x_user_id="123", notes="no credentials here")
        service.link_token_identity("token", link_type="developer_profile", source="operator_manual", profile_url="https://x.com/safe?token=SECRET")

    logs = "\n".join(record.getMessage() for record in caplog.records)
    links = service.list_token_links_for_identity("secret-test")
    payload = str(links)
    assert "SECRET" not in logs
    assert "SECRET" not in payload

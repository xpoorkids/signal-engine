from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app import main
from app.services.action_engine_service import ActionEngineService
from app.services.x_identity_service import XIdentityService


TOKEN = "FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2"


def _good_market(**overrides):
    data = {
        "liquidity_usd": 100000,
        "volume_m5": 50000,
        "txns_m5_buys": 60,
        "txns_m5_sells": 8,
        "buy_route_ok": True,
        "sell_route_ok": True,
        "quote_fresh": True,
        "buy_impact_pct": 1.0,
        "sell_impact_pct": 1.5,
        "round_trip_cost_pct": 2.5,
        "organic_flow_windows": 3,
        "wallet_or_fee_confirmation": True,
        "maximum_safe_size_usd": 500,
        "data_confidence_pct": 85,
        "probability_target_before_invalidation_pct": 62,
        "estimated_net_return_pct": 15,
        "probability_rug_like_event_pct": 4,
        "probability_liquidity_failure_pct": 4,
        "total_fee_sol": 20,
        "kol_support": True,
    }
    data.update(overrides)
    return data


def _assessment():
    return {"attention_score": 0.95, "risk_score": 0.05, "rug_check": {"verdict": "low"}, "security": {}}


def _catalyst():
    return {"verification_status": "high_conviction", "catalyst_confidence_pct": 95, "catalyst_flow_confirmation": True}


def test_disabled_seed_block_survives_restart_and_reseed(tmp_path):
    db_path = tmp_path / "x.db"
    service = XIdentityService(db_path)
    seed = service.initialize_seed_blocklist()
    assert seed["status"] == "applied"
    assert service.get_identity("operator_blocked_repeated_coin_rebrands_1")["operator_block_status"] == "active"

    disabled = service.disable_block("operator_blocked_repeated_coin_rebrands_1", notes="operator cleared manually")
    disabled_ts = disabled["disabled_ts"]
    assert disabled["operator_block_status"] == "disabled"
    assert disabled_ts is not None

    service = XIdentityService(db_path)
    engine = ActionEngineService(db_path, x_identities=service)
    engine.init_schema()
    unrelated = engine.recommend_for_token("unrelated-token", market=_good_market(), assessment=_assessment(), persist=False)
    assert unrelated["x_identity_risk"]["action"] is None

    reseed = service.initialize_seed_blocklist()
    assert reseed["status"] == "already_applied"
    still_disabled = service.get_identity("operator_blocked_repeated_coin_rebrands_1")
    assert still_disabled["operator_block_status"] == "disabled"
    assert still_disabled["disabled_ts"] == disabled_ts
    assert still_disabled["restored_ts"] is None

    restored = service.restore_block("operator_blocked_repeated_coin_rebrands_1", notes="explicit operator restore")
    assert restored["operator_block_status"] == "active"
    assert restored["restored_ts"] is not None

    restarted = XIdentityService(db_path)
    restarted.ensure_seeded_once()
    active = restarted.get_identity("operator_blocked_repeated_coin_rebrands_1")
    assert active["operator_block_status"] == "active"
    assert active["restored_ts"] == restored["restored_ts"]


def test_concurrent_seed_initialization_is_idempotent(tmp_path):
    db_path = tmp_path / "x.db"

    def apply_seed():
        return XIdentityService(db_path).initialize_seed_blocklist()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply_seed(), range(2)))

    statuses = {result["status"] for result in results}
    assert statuses <= {"applied", "already_applied"}
    service = XIdentityService(db_path)
    with service._connect() as conn:
        identities = conn.execute("SELECT COUNT(*) AS c FROM x_identities").fetchone()["c"]
        aliases = conn.execute("SELECT COUNT(*) AS c FROM x_identity_aliases").fetchone()["c"]
        migrations = conn.execute("SELECT COUNT(*) AS c FROM x_identity_seed_migrations").fetchone()["c"]
    assert identities == 2
    assert aliases == 17
    assert migrations == 1


def test_x_identity_management_routes_require_operator_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "routes.db"))
    monkeypatch.delenv("SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED", raising=False)
    monkeypatch.setenv("SIGNAL_ENGINE_OPERATOR_API_TOKEN", "correct-route-token")
    client = TestClient(main.app)

    assert client.post("/x-identities/seed").status_code == 404

    monkeypatch.setenv("SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED", "1")
    assert client.post("/x-identities/seed").status_code == 401
    bad = client.post("/x-identities/seed", headers={"Authorization": "Bearer wrong-route-token"})
    assert bad.status_code == 401
    assert "wrong-route-token" not in bad.text

    ok = client.post("/x-identities/seed", headers={"Authorization": "Bearer correct-route-token"})
    assert ok.status_code == 200

    audit_payload = str(XIdentityService(tmp_path / "routes.db").list_audit_log())
    assert "wrong-route-token" not in audit_payload
    assert "correct-route-token" not in audit_payload


def test_sensitive_reads_are_protected_by_default_and_public_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "reads.db"))
    monkeypatch.setenv("SIGNAL_ENGINE_OPERATOR_API_TOKEN", "read-token")
    monkeypatch.setenv("SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED", "1")
    monkeypatch.delenv("SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC", raising=False)
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer read-token"}
    assert client.post("/x-identities/seed", headers=headers).status_code == 200
    assert client.get("/x-identities/blocked").status_code == 401
    assert client.get("/x-identities/blocked", headers=headers).status_code == 200

    monkeypatch.setenv("SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC", "1")
    assert client.get("/x-identities/blocked").status_code == 200
    assert client.post("/x-identities/seed").status_code == 401


def test_stable_x_user_id_conflict_preserves_records_and_audits(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.add_blocked_identity(identity_id="one", current_handle="@one", stable_x_user_id="12345")
    service.add_blocked_identity(identity_id="two", current_handle="@two")
    before_one = service.get_identity("one")
    before_two = service.get_identity("two")

    try:
        service.add_stable_x_user_id("two", "12345")
    except Exception as exc:
        assert str(exc) == "stable_x_user_id_conflict"
    else:
        raise AssertionError("stable_x_user_id_conflict was not raised")

    assert service.get_identity("one")["stable_x_user_id"] == before_one["stable_x_user_id"]
    assert service.get_identity("two")["stable_x_user_id"] == before_two["stable_x_user_id"]
    assert any(row["action"] == "stable_id_conflict" for row in service.list_audit_log())


def test_official_dex_x_url_is_ingested_and_blocks_positive_action(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)
    rec = engine.recommend_for_token(
        TOKEN,
        market=_good_market(info={"socials": [{"type": "twitter", "url": "https://x.com/Datboicoincto?ref=dex"}]}),
        assessment=_assessment(),
        catalyst=_catalyst(),
        persist=False,
    )
    assert rec["action"] == "AVOID"
    assert rec["recommended_initial_size_pct"] == 0
    assert "blocked_x_handle_match_unresolved" in rec["blockers"]
    links = service.evaluate_token(TOKEN).to_dict()
    assert links["action"] == "AVOID"


def test_verified_rename_lineage_from_official_link_hard_fails(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)
    rec = engine.recommend_for_token(
        TOKEN,
        market=_good_market(x_identity_links=[{"handle": "@Trotcatcoin", "link_type": "official_token_social", "match_method": "operator_verified_alias_lineage"}]),
        assessment=_assessment(),
        catalyst=_catalyst(),
        persist=False,
    )
    assert rec["action"] == "HARD FAIL"
    assert "operator_blocked_x_identity" in rec["blockers"]
    assert "blocked_x_rename_lineage" in rec["blockers"]


def test_stable_id_match_hard_fails_after_handle_rename(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.initialize_seed_blocklist()
    service.add_stable_x_user_id("operator_blocked_repeated_coin_rebrands_1", "555555")
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)
    rec = engine.recommend_for_token(
        TOKEN,
        market=_good_market(x_identity_links=[{"stable_x_user_id": "555555", "handle": "@newcoincto", "link_type": "developer_profile"}]),
        assessment=_assessment(),
        catalyst=_catalyst(),
        persist=False,
    )
    assert rec["action"] == "HARD FAIL"
    assert rec["x_identity_risk"]["match_method"] == "stable_x_user_id"


def test_handle_reuse_by_different_stable_id_requires_review_without_merging(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    service.add_blocked_identity(identity_id="blocked", current_handle="@oldhandle", stable_x_user_id="12345")
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)
    rec = engine.recommend_for_token(
        TOKEN,
        market=_good_market(x_identity_links=[{"stable_x_user_id": "99999", "handle": "@oldhandle", "link_type": "official_token_social"}]),
        assessment=_assessment(),
        catalyst=_catalyst(),
        persist=False,
    )
    assert rec["action"] != "HARD FAIL"
    assert "X IDENTITY REVIEW REQUIRED" in rec["warnings"]
    assert rec["x_identity_risk"]["match_method"] == "handle_reuse_stable_id_conflict"
    token_links = service.evaluate_token(TOKEN).to_dict()
    assert token_links["matched_identity_id"] == "blocked"
    with service._connect() as conn:
        linked_identity = conn.execute("SELECT identity_id FROM x_identity_token_links WHERE token=?", (TOKEN,)).fetchone()["identity_id"]
    assert linked_identity is None


def test_incidental_blocked_mention_does_not_hard_fail(tmp_path):
    service = XIdentityService(tmp_path / "x.db")
    engine = ActionEngineService(tmp_path / "x.db", x_identities=service)
    rec = engine.recommend_for_token(
        TOKEN,
        market=_good_market(x_identity_links=[{"handle": "@Datboicoincto", "link_type": "mention_only"}]),
        assessment=_assessment(),
        catalyst=_catalyst(),
        persist=False,
    )
    assert rec["action"] != "HARD FAIL"
    assert rec["action"] != "AVOID"
    assert "BLOCKED IDENTITY PROMOTION EXPOSURE" in rec["warnings"]

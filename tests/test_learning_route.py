from __future__ import annotations

from fastapi.testclient import TestClient

from app import main
from app.services import signal_learning_service as sls


def test_learning_report_route_returns_latest(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    report = sls.generate_daily_learning_report("2026-03-14")
    client = TestClient(main.app)
    response = client.get("/learning/report/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_date"] == report["report_date"]


def test_learning_report_latest_dashboard_returns_html(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.generate_daily_learning_report("2026-03-14")

    client = TestClient(main.app)
    response = client.get("/learning/report/latest/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Daily Learning Report" in response.text


def test_learning_report_latest_digest_returns_json_and_html(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.generate_daily_learning_report("2026-03-14")

    client = TestClient(main.app)

    json_response = client.get("/learning/report/latest/digest")
    assert json_response.status_code == 200
    payload = json_response.json()
    assert "highlights" in payload

    html_response = client.get("/learning/report/latest/digest/dashboard")
    assert html_response.status_code == 200
    assert "Learning Digest" in html_response.text


def test_learning_report_route_404_when_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    client = TestClient(main.app)
    response = client.get("/learning/report/latest")

    assert response.status_code == 404

    dashboard_response = client.get("/learning/report/latest/dashboard")
    assert dashboard_response.status_code == 404
    digest_response = client.get("/learning/report/latest/digest")
    assert digest_response.status_code == 404
    digest_dashboard_response = client.get("/learning/report/latest/digest/dashboard")
    assert digest_dashboard_response.status_code == 404


def test_learning_diagnostics_route_returns_summary(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.05,
        risk_score=0.50,
        confidence_score=0.20,
        creator_score=0.0,
        lifecycle="dex",
    )

    client = TestClient(main.app)
    response = client.get("/learning/diagnostics/summary?hours=24")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts_by_decision"]["candidate_gate_skip"] == 1


def test_learning_engine_health_routes_return_status(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.05,
        risk_score=0.50,
        confidence_score=0.20,
        creator_score=0.0,
        lifecycle="dex",
        ts_value=1_773_500_000,
    )

    client = TestClient(main.app)

    json_response = client.get("/learning/health?hours=10000")
    assert json_response.status_code == 200
    payload = json_response.json()
    assert payload["status"] in {"cold", "quiet", "processing", "gated", "blocked", "active"}
    assert "skip_pressure" in payload
    assert "storage" in payload

    html_response = client.get("/learning/health/dashboard?hours=10000")
    assert html_response.status_code == 200
    assert "Engine Health" in html_response.text
    assert "DB Path" in html_response.text


def test_learning_policy_routes_return_traces_and_shadow_eval(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    signal_id = sls.record_signal_decision(
        token="token-policy",
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        reasons=["improved_attention"],
        features={
            "attention_score": 0.74,
            "creator_score": 0.42,
            "candidate_rate_limit_allowed": True,
            "candidate_progression_ok": True,
            "candidate_send_eligible": True,
        },
        attention_score=0.74,
        risk_score=0.18,
        confidence_score=0.67,
        creator_score=0.42,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="policy-live-1",
        ts_value=1_773_600_500,
        source="test",
    )
    with sls._connect() as c:
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
                1_773_604_100,
                "dex",
                22000,
                6400,
                4500,
                60.0,
                28.0,
                70.0,
                48,
                20,
                85.0,
                5.0,
                30.0,
                "worked",
                '{"outcome_label":"worked"}',
            ),
        )

    client = TestClient(main.app)

    traces_response = client.get("/learning/policy/traces?hours=10000&limit=10&stage=candidate")
    assert traces_response.status_code == 200
    traces_payload = traces_response.json()
    assert traces_payload["trace_count"] == 1
    assert traces_payload["traces"][0]["action_taken"] == "emit"
    assert traces_payload["traces"][0]["policy_version"] == "policy-live-1"

    shadow_response = client.get(
        "/learning/policy/shadow?hours=10000&stage=candidate&candidate_attention_min=0.80&policy_version=shadow-2"
    )
    assert shadow_response.status_code == 200
    shadow_payload = shadow_response.json()
    assert shadow_payload["changed_count"] == 1
    assert shadow_payload["changed_examples"][0]["shadow_action"] == "hold"
    assert shadow_payload["impact"]["positive_outcomes"] == 1


def test_learning_policy_replay_routes_run_and_fetch_results(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    signal_id = sls.record_signal_decision(
        token="token-replay-route",
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        reasons=["improved_attention"],
        features={
            "attention_score": 0.74,
            "creator_score": 0.42,
            "candidate_rate_limit_allowed": True,
            "candidate_progression_ok": True,
            "candidate_send_eligible": True,
        },
        attention_score=0.74,
        risk_score=0.18,
        confidence_score=0.67,
        creator_score=0.42,
        lifecycle="dex",
        policy_name="deterministic_engine",
        policy_version="policy-live-1",
        ts_value=1_773_600_500,
        source="test",
    )
    with sls._connect() as c:
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
                1_773_604_100,
                "dex",
                22000,
                6400,
                4500,
                60.0,
                28.0,
                70.0,
                48,
                20,
                85.0,
                5.0,
                30.0,
                "worked",
                '{"outcome_label":"worked"}',
            ),
        )

    client = TestClient(main.app)
    run_response = client.post(
        "/learning/policy/replay/run",
        json={
            "hours": 10000,
            "stage": "candidate",
            "policy_name": "shadow_policy",
            "policy_version": "shadow-3",
            "candidate_attention_min": 0.80,
        },
    )
    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert run_payload["changed_count"] == 1

    latest_response = client.get("/learning/policy/replay/latest")
    assert latest_response.status_code == 200
    latest_payload = latest_response.json()
    assert latest_payload["run_id"] == run_payload["run_id"]
    assert latest_payload["results"][0]["changed"] is True

    by_id_response = client.get(f"/learning/policy/replay/{run_payload['run_id']}")
    assert by_id_response.status_code == 200
    by_id_payload = by_id_response.json()
    assert by_id_payload["run_id"] == run_payload["run_id"]
    assert by_id_payload["results"][0]["shadow_action"] == "hold"


def test_learning_policy_profile_and_rollout_routes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    client = TestClient(main.app)
    profile_response = client.post(
        "/learning/policy/profiles",
        json={
            "policy_name": "adaptive_candidate",
            "policy_version": "v1",
            "description": "candidate strict profile",
            "created_by": "ops",
            "config": {"candidate_creator_min": 0.55, "promoted_liquidity_min": 22000.0},
        },
    )
    assert profile_response.status_code == 200
    profile_payload = profile_response.json()
    assert profile_payload["policy_name"] == "adaptive_candidate"

    list_profiles_response = client.get("/learning/policy/profiles?limit=10")
    assert list_profiles_response.status_code == 200
    assert list_profiles_response.json()["profiles"]

    rollout_response = client.post(
        "/learning/policy/rollouts",
        json={
            "policy_name": "adaptive_candidate",
            "policy_version": "v1",
            "rollout_mode": "active",
            "stage_scope": "candidate",
            "traffic_percent": 100,
            "priority": 10,
            "activated_by": "ops",
        },
    )
    assert rollout_response.status_code == 200
    rollout_payload = rollout_response.json()
    assert rollout_payload["policy_name"] == "adaptive_candidate"

    list_rollouts_response = client.get("/learning/policy/rollouts?active_only=true")
    assert list_rollouts_response.status_code == 200
    assert list_rollouts_response.json()["rollouts"]

    resolve_response = client.get("/learning/policy/resolve?stage=candidate&token=token-123")
    assert resolve_response.status_code == 200
    resolve_payload = resolve_response.json()
    assert resolve_payload["policy_name"] == "adaptive_candidate"
    assert resolve_payload["config"]["candidate_creator_min"] == 0.55


def test_learning_policy_regime_routes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    sls.create_policy_profile(
        policy_name="adaptive_candidate",
        policy_version="v1",
        created_by="ops",
        config={"candidate_creator_min": 0.55},
    )
    rollout_response = TestClient(main.app).post(
        "/learning/policy/rollouts",
        json={
            "policy_name": "adaptive_candidate",
            "policy_version": "v1",
            "rollout_mode": "active",
            "stage_scope": "candidate",
            "regime_scope": "candidate|us_day|mid|developing|building",
            "priority": 5,
            "activated_by": "ops",
        },
    )
    assert rollout_response.status_code == 200
    assert rollout_response.json()["regime_scope"] == "candidate|us_day|mid|developing|building"

    sls.record_signal_decision(
        token="token-regime-route",
        event_type="candidate",
        stage="candidate",
        decision="candidate_ready",
        action_taken="emit",
        features={
            "session_bucket": "us_day",
            "liquidity_usd": 18000,
            "age_minutes": 22,
            "price_change_m5": 18.0,
            "price_change_h1": 55.0,
            "unique_buyers_15m": 44,
        },
        attention_score=0.76,
        risk_score=0.20,
        confidence_score=0.70,
        creator_score=0.41,
        lifecycle="dex",
        ts_value=1_773_600_500,
        source="test",
    )

    client = TestClient(main.app)
    resolve_response = client.get(
        "/learning/policy/resolve?stage=candidate&token=token-123&regime_key=candidate|us_day|mid|developing|building"
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["regime_scope"] == "candidate|us_day|mid|developing|building"

    regimes_response = client.get("/learning/policy/regimes?hours=10000&limit=10")
    assert regimes_response.status_code == 200
    payload = regimes_response.json()
    assert payload["regimes"]
    assert payload["regimes"][0]["regime_key"] == "candidate|us_day|mid|developing|building"


def test_learning_policy_approval_and_guardrail_routes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    client = TestClient(main.app)
    profile_response = client.post(
        "/learning/policy/profiles",
        json={
            "policy_name": "canary_guarded",
            "policy_version": "v1",
            "created_by": "ops",
            "config": {"promoted_risk_max": 0.45},
        },
    )
    assert profile_response.status_code == 200

    approval_response = client.post(
        "/learning/policy/approvals",
        json={
            "policy_name": "canary_guarded",
            "policy_version": "v1",
            "source_type": "profile",
            "approved_by": "ops",
        },
    )
    assert approval_response.status_code == 200
    approval_id = approval_response.json()["approval_id"]

    approval_status_response = client.post(
        f"/learning/policy/approvals/{approval_id}/status",
        json={"approval_status": "approved", "approved_by": "ops"},
    )
    assert approval_status_response.status_code == 200
    assert approval_status_response.json()["approval_status"] == "approved"

    rollout_response = client.post(
        "/learning/policy/rollouts",
        json={
            "policy_name": "canary_guarded",
            "policy_version": "v1",
            "rollout_mode": "canary",
            "stage_scope": "promoted",
            "traffic_percent": 100,
            "priority": 1,
            "activated_by": "ops",
        },
    )
    assert rollout_response.status_code == 200

    for idx in range(3):
        signal_id = sls.record_signal_decision(
            token=f"token-canary-route-{idx}",
            event_type="promoted",
            stage="promoted",
            decision="promoted_sent",
            action_taken="emit",
            attention_score=0.7,
            risk_score=0.5,
            confidence_score=0.9,
            creator_score=0.3,
            lifecycle="dex",
            policy_name="canary_guarded",
            policy_version="v1",
            ts_value=1_773_700_100 + idx,
            source="test",
        )
        with sls._connect() as c:
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
                    1_773_703_700 + idx,
                    "dex",
                    7000,
                    2500,
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
                    '{"outcome_label":"failed"}',
                ),
            )

    guardrail_response = client.post(
        "/learning/policy/guardrails/evaluate",
        json={"hours": 10000, "min_samples": 3, "max_negative_rate": 60.0, "auto_apply": True},
    )
    assert guardrail_response.status_code == 200
    assert guardrail_response.json()["evaluations"][0]["recommended_action"] == "rollback"

    approvals_response = client.get("/learning/policy/approvals?limit=10")
    assert approvals_response.status_code == 200
    assert approvals_response.json()["approvals"]

    events_response = client.get("/learning/policy/events?limit=20")
    assert events_response.status_code == 200
    assert any(item["event_type"] == "guardrail_rollback" for item in events_response.json()["events"])


def test_learning_policy_automation_routes(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    client = TestClient(main.app)
    assert client.post(
        "/learning/policy/profiles",
        json={
            "policy_name": "baseline_policy",
            "policy_version": "v1",
            "created_by": "ops",
            "config": {"promoted_risk_max": 0.60},
        },
    ).status_code == 200
    assert client.post(
        "/learning/policy/rollouts",
        json={
            "policy_name": "baseline_policy",
            "policy_version": "v1",
            "rollout_mode": "active",
            "stage_scope": "promoted",
            "traffic_percent": 100,
            "priority": 20,
            "activated_by": "ops",
        },
    ).status_code == 200
    assert client.post(
        "/learning/policy/profiles",
        json={
            "policy_name": "auto_policy",
            "policy_version": "v2",
            "created_by": "ops",
            "config": {"promoted_risk_max": 0.40},
        },
    ).status_code == 200

    replay_signal_id = sls.record_signal_decision(
        token="token-auto-route-replay",
        event_type="promoted",
        stage="promoted",
        decision="promoted_sent",
        action_taken="emit",
        attention_score=0.82,
        risk_score=0.31,
        confidence_score=0.92,
        creator_score=0.3,
        lifecycle="dex",
        policy_name="baseline_policy",
        policy_version="v1",
        features={
            "attention_score": 0.82,
            "risk_score": 0.31,
            "confidence_score": 0.92,
            "liquidity_usd": 22000.0,
            "txns_m5_buys": 22,
        },
        ts_value=1_773_820_000,
        source="test",
    )
    with sls._connect() as c:
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
                replay_signal_id,
                60,
                1_773_823_600,
                "dex",
                21000,
                11000,
                7500,
                80.0,
                18.0,
                45.0,
                48,
                16,
                60.0,
                20.0,
                35.0,
                "worked",
                '{"outcome_label":"worked"}',
            ),
        )
    replay_response = client.post(
        "/learning/policy/replay/run",
        json={
            "hours": 10000,
            "stage": "promoted",
            "policy_name": "auto_policy",
            "policy_version": "v2",
            "promoted_risk_max": 0.20,
        },
    )
    assert replay_response.status_code == 200

    for idx in range(3):
        canary_signal_id = sls.record_signal_decision(
            token=f"token-auto-route-canary-{idx}",
            event_type="promoted",
            stage="promoted",
            decision="promoted_sent",
            action_taken="emit",
            attention_score=0.86,
            risk_score=0.22,
            confidence_score=0.94,
            creator_score=0.35,
            lifecycle="dex",
            policy_name="auto_policy",
            policy_version="v2",
            ts_value=1_773_830_000 + idx,
            source="test",
        )
        with sls._connect() as c:
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
                    canary_signal_id,
                    60,
                    1_773_833_600 + idx,
                    "dex",
                    26000,
                    12000,
                    8000,
                    70.0,
                    24.0,
                    60.0,
                    60,
                    18,
                    75.0,
                    18.0,
                    40.0,
                    "strong_continuation",
                    '{"outcome_label":"strong_continuation"}',
                ),
            )

    status_response = client.get("/learning/policy/automation/status")
    assert status_response.status_code == 200
    assert "config" in status_response.json()
    assert "guardrails" in status_response.json()

    run_response = client.post("/learning/policy/automation/run", json={"hours": 10000, "replay_limit": 10})
    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert "generated" in run_payload
    assert run_payload["approvals"]["created"]
    assert run_payload["canaries"]["scheduled"]
    assert run_payload["promotions"]["promoted"]
    assert run_payload["status"] == "completed"

    generation_response = client.post(
        "/learning/policy/automation/generate",
        json={"hours": 10000, "generation_limit": 4, "replay_limit": 200},
    )
    assert generation_response.status_code == 200
    assert "generated" in generation_response.json()

    latest_run_response = client.get("/learning/policy/automation/runs/latest")
    assert latest_run_response.status_code == 200
    assert latest_run_response.json()["run_id"] == run_payload["run_id"]

    runs_response = client.get("/learning/policy/automation/runs?limit=5")
    assert runs_response.status_code == 200
    assert runs_response.json()["runs"][0]["run_id"] == run_payload["run_id"]

    approvals_response = client.get("/learning/policy/approvals?limit=10")
    assert approvals_response.status_code == 200
    assert any(item["policy_name"] == "auto_policy" for item in approvals_response.json()["approvals"])

    events_response = client.get("/learning/policy/events?limit=20")
    assert events_response.status_code == 200
    event_types = {item["event_type"] for item in events_response.json()["events"]}
    assert "auto_approval_created" in event_types
    assert "auto_canary_started" in event_types
    assert "canary_promoted" in event_types


def test_learning_tuning_proposals_route_returns_config_suggestions(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SERVICE", "worker")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SHA", "auto123")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_ENV", "production")
    monkeypatch.setenv("SIGNAL_ENGINE_REQUIRED_ALIGNED_PROFILES", "strict")
    sls.init()

    base_ts = 1_773_620_000
    positive_ids = []
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
                    '{"outcome_label":"worked"}',
                ),
            )

    client = TestClient(main.app)
    response = client.get("/learning/tuning/proposals?hours=10000")

    assert response.status_code == 200
    payload = response.json()
    assert "proposals" in payload
    assert payload["proposal_count"] >= 1

    dashboard_response = client.get("/learning/tuning/proposals/dashboard?hours=10000")
    assert dashboard_response.status_code == 200
    assert "Tuning Proposals" in dashboard_response.text
    assert ".env Snippet" in dashboard_response.text
    assert "Apply Manually Diff" in dashboard_response.text

    env_response = client.get("/learning/tuning/proposals/env?hours=10000")
    assert env_response.status_code == 200
    assert "text/plain" in env_response.headers["content-type"]
    assert "EARLY_ATTENTION_MIN=" in env_response.text

    diff_response = client.get("/learning/tuning/proposals/diff?hours=10000")
    assert diff_response.status_code == 200
    assert "text/plain" in diff_response.headers["content-type"]
    assert "EARLY_ATTENTION_MIN:" in diff_response.text

    profiles_response = client.get("/learning/tuning/profiles?hours=10000")
    assert profiles_response.status_code == 200
    profiles_payload = profiles_response.json()
    assert profiles_payload["base_profile"] == "balanced"
    assert "strict" in profiles_payload["profiles"]

    profiles_dashboard_response = client.get("/learning/tuning/profiles/dashboard?hours=10000")
    assert profiles_dashboard_response.status_code == 200
    assert "Tuning Profiles" in profiles_dashboard_response.text
    assert "Balanced" in profiles_dashboard_response.text

    strict_env_response = client.get("/learning/tuning/profiles/strict/env?hours=10000")
    assert strict_env_response.status_code == 200
    assert "PROM_MIN_LIQ_USD=" in strict_env_response.text

    aggressive_diff_response = client.get("/learning/tuning/profiles/aggressive/diff?hours=10000")
    assert aggressive_diff_response.status_code == 200
    assert "EARLY_ATTENTION_MIN:" in aggressive_diff_response.text

    create_approval_response = client.post(
        "/learning/tuning/approvals",
        json={
            "approval_kind": "profile",
            "target_name": "strict",
            "artifact_kind": "env",
            "hours": 10000,
            "approved_by": "ops",
            "notes": "ready for manual rollout",
        },
    )
    assert create_approval_response.status_code == 200
    approval_payload = create_approval_response.json()
    assert approval_payload["approval_kind"] == "profile"
    assert approval_payload["target_name"] == "strict"
    assert approval_payload["rollout_status"] == "pending"

    approvals_response = client.get("/learning/tuning/approvals?limit=10")
    assert approvals_response.status_code == 200
    approvals_payload = approvals_response.json()
    assert approvals_payload["approvals"]

    approval_id = approval_payload["approval_id"]
    approve_response = client.post(
        f"/learning/tuning/approvals/{approval_id}/status",
        json={"rollout_status": "approved", "notes": "approved for deploy"},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["rollout_status"] == "approved"

    status_response = client.post(
        f"/learning/tuning/approvals/{approval_id}/status",
        json={
            "rollout_status": "rolled_out",
            "notes": "rolled to render",
        },
    )
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["rollout_status"] == "rolled_out"
    assert status_payload["deployment_service"] == "worker"
    assert status_payload["deployment_sha"] == "auto123"

    latest_response = client.get(
        "/learning/tuning/approvals/latest?approval_kind=profile&target_name=strict&artifact_kind=env&rollout_status=rolled_out"
    )
    assert latest_response.status_code == 200
    latest_payload = latest_response.json()
    assert latest_payload["approval_id"] == approval_id

    latest_artifact_response = client.get(
        "/learning/tuning/approvals/latest/artifact?approval_kind=profile&target_name=strict&artifact_kind=env&rollout_status=rolled_out"
    )
    assert latest_artifact_response.status_code == 200
    assert "PROM_MIN_LIQ_USD=" in latest_artifact_response.text

    filtered_approvals_response = client.get("/learning/tuning/approvals?limit=10&rollout_status=rolled_out&q=render")
    assert filtered_approvals_response.status_code == 200
    filtered_payload = filtered_approvals_response.json()
    assert filtered_payload["approvals"]

    latest_bundle_response = client.get("/learning/tuning/approvals/latest/bundle?artifact_kind=env&rollout_status=rolled_out")
    assert latest_bundle_response.status_code == 200
    assert "[strict]" in latest_bundle_response.text

    drift_response = client.get("/learning/tuning/drift?target_name=strict&rollout_status=rolled_out")
    assert drift_response.status_code == 200
    drift_payload = drift_response.json()
    assert drift_payload["target_name"] == "strict"
    assert "drift_count" in drift_payload

    rollout_summary_response = client.get("/learning/tuning/rollout/summary")
    assert rollout_summary_response.status_code == 200
    rollout_summary_payload = rollout_summary_response.json()
    assert rollout_summary_payload["latest_by_service"]["worker"]["approval_id"] == approval_id
    assert rollout_summary_payload["notifications"]
    assert rollout_summary_payload["recommended_actions"]

    rollout_dashboard_response = client.get("/learning/tuning/rollout/dashboard")
    assert rollout_dashboard_response.status_code == 200
    assert "Tuning Rollout Summary" in rollout_dashboard_response.text
    assert "Recommended Actions" in rollout_dashboard_response.text

    verification_response = client.get("/learning/tuning/verification?approval_id=" + approval_id)
    assert verification_response.status_code == 200
    verification_payload = verification_response.json()
    assert verification_payload["approval"]["approval_id"] == approval_id
    assert "verification_status" in verification_payload
    assert "changed_config" in verification_payload
    assert "family_scorecards" in verification_payload

    verification_dashboard_response = client.get("/learning/tuning/verification/dashboard?approval_id=" + approval_id)
    assert verification_dashboard_response.status_code == 200
    assert "Rollout Verification" in verification_dashboard_response.text
    assert "Changed Config" in verification_dashboard_response.text
    assert "Historical Family Scorecards" in verification_dashboard_response.text

    verification_apply_response = client.post(
        "/learning/tuning/verification/apply",
        json={"approval_id": approval_id, "baseline_hours": 24, "post_hours": 24},
    )
    assert verification_apply_response.status_code == 200
    verification_apply_payload = verification_apply_response.json()
    assert verification_apply_payload["approval"]["approval_id"] == approval_id
    assert "verification_status" in verification_apply_payload["approval"]

    verification_run_response = client.post(
        "/learning/tuning/verification/run",
        json={"baseline_hours": 24, "post_hours": 24, "limit": 10, "force": True},
    )
    assert verification_run_response.status_code == 200
    verification_run_payload = verification_run_response.json()
    assert "applied_count" in verification_run_payload
    assert "skipped_count" in verification_run_payload

    notifications_response = client.get("/learning/tuning/notifications?limit=20")
    assert notifications_response.status_code == 200
    notifications_payload = notifications_response.json()
    assert notifications_payload["notifications"]
    assert any(item["event_type"] == "drift_resolved" for item in notifications_payload["notifications"])

    active_notifications_response = client.get("/learning/tuning/notifications?limit=20&active_only=true")
    assert active_notifications_response.status_code == 200
    active_notifications_payload = active_notifications_response.json()
    assert active_notifications_payload["notifications"]

    incidents_response = client.get("/learning/tuning/incidents?limit=20")
    assert incidents_response.status_code == 200
    incidents_payload = incidents_response.json()
    assert incidents_payload["incidents"]

    notifications_dashboard_response = client.get("/learning/tuning/notifications/dashboard?limit=20")
    assert notifications_dashboard_response.status_code == 200
    assert "Rollout Notifications" in notifications_dashboard_response.text
    assert "Active" in notifications_dashboard_response.text

    incidents_dashboard_response = client.get("/learning/tuning/incidents/dashboard?limit=20")
    assert incidents_dashboard_response.status_code == 200
    assert "Notification Incidents" in incidents_dashboard_response.text
    assert "State" in incidents_dashboard_response.text

    first_notification_id = notifications_payload["notifications"][0]["notification_id"]
    ack_response = client.post(
        f"/learning/tuning/notifications/{first_notification_id}/state",
        json={"acknowledged": True, "acknowledged_by": "ops-user"},
    )
    assert ack_response.status_code == 200
    ack_payload = ack_response.json()
    assert ack_payload["acknowledged_by"] == "ops-user"

    active_after_ack = client.get("/learning/tuning/notifications?limit=20&active_only=true")
    assert active_after_ack.status_code == 200
    assert all(item["notification_id"] != first_notification_id for item in active_after_ack.json()["notifications"])

    unsnooze_response = client.post(
        f"/learning/tuning/notifications/{first_notification_id}/state",
        json={"acknowledged": False, "snooze_minutes": 15},
    )
    assert unsnooze_response.status_code == 200
    assert unsnooze_response.json()["snoozed_until_ts"] is not None

    incident_ack_response = client.post(
        "/learning/tuning/incidents/state",
        json={
            "event_type": incidents_payload["incidents"][0]["event_type"],
            "target_name": incidents_payload["incidents"][0]["target_name"],
            "deployment_service": incidents_payload["incidents"][0]["deployment_service"],
            "acknowledged": True,
            "acknowledged_by": "ops-user",
        },
    )
    assert incident_ack_response.status_code == 200
    assert incident_ack_response.json()["state"] == "acknowledged"

    incident_resolve_response = client.post(
        "/learning/tuning/incidents/state",
        json={
            "event_type": incidents_payload["incidents"][0]["event_type"],
            "target_name": incidents_payload["incidents"][0]["target_name"],
            "deployment_service": incidents_payload["incidents"][0]["deployment_service"],
            "resolved": True,
            "resolved_by": "ops-user",
            "resolution_note": "Handled by operator",
        },
    )
    assert incident_resolve_response.status_code == 200
    assert incident_resolve_response.json()["state"] == "resolved"

    command_center_response = client.get("/learning/command-center?hours=24")
    assert command_center_response.status_code == 200
    command_center_payload = command_center_response.json()
    assert "engine_health" in command_center_payload
    assert "rollout_summary" in command_center_payload
    assert "drift" in command_center_payload
    assert "storage" in command_center_payload
    assert "incident_state_counts" in command_center_payload
    assert "rollout_verification_cards" in command_center_payload
    assert "rollout_verification_family_scorecards" in command_center_payload
    assert "policy_profiles" in command_center_payload
    assert "policy_rollouts" in command_center_payload
    assert "latest_policy_replay" in command_center_payload
    assert "policy_approvals" in command_center_payload
    assert "policy_events" in command_center_payload
    assert "policy_guardrails" in command_center_payload
    assert "policy_regimes" in command_center_payload
    assert "strongest_regimes" in command_center_payload
    assert "weakest_regimes" in command_center_payload
    assert "policy_automation" in command_center_payload
    assert "resolved_policies" in command_center_payload
    assert all("changed_keys" in item for item in command_center_payload["rollout_verification_cards"])

    command_center_dashboard_response = client.get("/learning/command-center/dashboard?hours=24")
    assert command_center_dashboard_response.status_code == 200
    assert "Operator Command Center" in command_center_dashboard_response.text
    assert "Health Snapshot" in command_center_dashboard_response.text
    assert "Policy Ops" in command_center_dashboard_response.text
    assert "Regime Intelligence" in command_center_dashboard_response.text
    assert "Regime Performance" in command_center_dashboard_response.text
    assert "Strongest Regimes" in command_center_dashboard_response.text
    assert "Weakest Regimes" in command_center_dashboard_response.text
    assert "Policy Rollouts" in command_center_dashboard_response.text
    assert "Policy Guardrails" in command_center_dashboard_response.text
    assert "Policy Approvals" in command_center_dashboard_response.text
    assert "Policy Events" in command_center_dashboard_response.text
    assert "Policy Profiles" in command_center_dashboard_response.text
    assert "Incident Snapshot" in command_center_dashboard_response.text
    assert "Rollout Verification" in command_center_dashboard_response.text
    assert "Verification Family Scorecards" in command_center_dashboard_response.text
    assert "Policy Automation:" in command_center_dashboard_response.text
    assert "Automation Runs" in command_center_dashboard_response.text
    assert "Families:" in command_center_dashboard_response.text or "Keys:" in command_center_dashboard_response.text

    ops_digest_response = client.get("/learning/ops/digest?hours=24")
    assert ops_digest_response.status_code == 200
    ops_digest_payload = ops_digest_response.json()
    assert "severity" in ops_digest_payload
    assert "summary" in ops_digest_payload

    ops_digest_dashboard_response = client.get("/learning/ops/digest/dashboard?hours=24")
    assert ops_digest_dashboard_response.status_code == 200
    assert "Ops Digest" in ops_digest_dashboard_response.text

    ops_digest_text_response = client.get("/learning/ops/digest/text?hours=24")
    assert ops_digest_text_response.status_code == 200
    assert "Signal Engine Ops Digest" in ops_digest_text_response.text

    ops_digest_send_response = client.post("/learning/ops/digest/send", json={"hours": 24, "force": True})
    assert ops_digest_send_response.status_code == 200
    ops_digest_send_payload = ops_digest_send_response.json()
    assert ops_digest_send_payload["dispatched"] is True
    assert ops_digest_send_payload["notification"]["event_type"] == "ops_digest"

    ops_digest_cooldown_response = client.post("/learning/ops/digest/send", json={"hours": 24})
    assert ops_digest_cooldown_response.status_code == 200
    ops_digest_cooldown_payload = ops_digest_cooldown_response.json()
    assert ops_digest_cooldown_payload["dispatched"] is False
    assert ops_digest_cooldown_payload["reason"] == "cooldown_unchanged_digest"

    approvals_dashboard_response = client.get("/learning/tuning/approvals/dashboard?limit=10&rollout_status=rolled_out&q=render")
    assert approvals_dashboard_response.status_code == 200
    assert "Tuning Approvals" in approvals_dashboard_response.text
    assert "Config Drift / Strict" in approvals_dashboard_response.text
    assert "worker" in approvals_dashboard_response.text

    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SERVICE", "engine")
    monkeypatch.setenv("SIGNAL_ENGINE_DEPLOY_SHA", "diff999")
    second_create_response = client.post(
        "/learning/tuning/approvals",
        json={
            "approval_kind": "profile",
            "target_name": "strict",
            "artifact_kind": "env",
            "hours": 10000,
            "approved_by": "ops",
            "notes": "engine rollout candidate",
        },
    )
    assert second_create_response.status_code == 200
    second_id = second_create_response.json()["approval_id"]
    second_approve_response = client.post(
        f"/learning/tuning/approvals/{second_id}/status",
        json={"rollout_status": "approved", "notes": "engine approved"},
    )
    assert second_approve_response.status_code == 200
    blocked_rollout_response = client.post(
        f"/learning/tuning/approvals/{second_id}/status",
        json={"rollout_status": "rolled_out", "notes": "should block"},
    )
    assert blocked_rollout_response.status_code == 400
    assert blocked_rollout_response.json()["detail"] == "alignment_guardrail_blocked"

    notifications_after_block = client.get("/learning/tuning/notifications?limit=30")
    assert notifications_after_block.status_code == 200
    blocked_payload = notifications_after_block.json()
    assert any(item["event_type"] == "rollout_blocked" for item in blocked_payload["notifications"])


def test_learning_diagnostics_dashboard_returns_html(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.05,
        risk_score=0.50,
        confidence_score=0.20,
        creator_score=0.0,
        lifecycle="dex",
    )

    client = TestClient(main.app)
    response = client.get("/learning/diagnostics/dashboard?hours=24")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Signal Diagnostics" in response.text
    assert "Top Skip Reasons" in response.text


def test_learning_diagnostics_routes_include_outcome_sections(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()

    signal_id = "sig-1"
    with sls._connect() as c:
        c.execute(
            """
            INSERT INTO signals (
                signal_id, external_ref, token, event_type, source, creator, alert_ts, updated_ts,
                lifecycle, confidence_score, attention_score, risk_score, elite_score,
                market_cap_usd, liquidity_usd, volume_m5_usd, age_minutes, price_change_m5,
                price_change_h1, txns_m5_buys, txns_m5_sells,
                hour_utc, day_of_week_utc, is_weekend_utc, hour_local, day_of_week_local,
                local_daypart, session_bucket, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                "ext-1",
                "token-a",
                "candidate",
                "test",
                None,
                1_773_500_000,
                1_773_500_000,
                "dex",
                0.4,
                0.18,
                0.2,
                7,
                10000,
                4000,
                2000,
                4.0,
                12.0,
                25.0,
                30,
                12,
                14,
                5,
                0,
                9,
                5,
                "morning",
                "us_day",
                "{}",
            ),
        )
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
                1_773_503_600,
                "dex",
                18000,
                4100,
                2600,
                64.0,
                25.0,
                60.0,
                44,
                20,
                80.0,
                2.5,
                30.0,
                "worked",
                '{"outcome_label":"worked"}',
            ),
        )
    sls.record_signal_decision(
        token="token-a",
        event_type="candidate",
        stage="candidate",
        decision="candidate_gate_skip",
        reasons=["attention<0.20"],
        attention_score=0.18,
        risk_score=0.20,
        confidence_score=0.40,
        creator_score=0.0,
        lifecycle="dex",
        ts_value=1_773_500_000,
    )

    client = TestClient(main.app)

    summary_response = client.get("/learning/diagnostics/summary?hours=10000")
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["false_negatives"]
    assert "conversion" in summary_payload
    assert "reason_quality" in summary_payload
    assert "threshold_guidance" in summary_payload
    assert "session_signal_quality" in summary_payload
    assert "reason_trends" in summary_payload
    assert "session_signal_trends" in summary_payload

    dashboard_response = client.get("/learning/diagnostics/dashboard?hours=10000")
    assert dashboard_response.status_code == 200
    assert "False Negatives" in dashboard_response.text
    assert "Session Outcome Quality" in dashboard_response.text
    assert "Session x Signal Quality" in dashboard_response.text
    assert "Blocker Outcome Scorecards" in dashboard_response.text
    assert "Threshold Guidance" in dashboard_response.text
    assert "Blocker Trends" in dashboard_response.text
    assert "Session x Signal Trends" in dashboard_response.text


def test_learning_report_dashboard_by_date_returns_tuning_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    monkeypatch.setattr(sls, "DB_PATH", db_path)
    sls.init()
    report = sls.generate_daily_learning_report("2026-03-14")

    client = TestClient(main.app)
    response = client.get(f"/learning/report/{report['report_date']}/dashboard")

    assert response.status_code == 200
    assert "Daily Learning Report" in response.text
    assert "Top Blockers" in response.text
    assert "Threshold Calls" in response.text

    digest_response = client.get(f"/learning/report/{report['report_date']}/digest")
    assert digest_response.status_code == 200
    digest_payload = digest_response.json()
    assert "highlights" in digest_payload

    digest_dashboard_response = client.get(f"/learning/report/{report['report_date']}/digest/dashboard")
    assert digest_dashboard_response.status_code == 200
    assert "Learning Digest" in digest_dashboard_response.text

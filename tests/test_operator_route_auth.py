from fastapi.testclient import TestClient

from app import main


def test_operator_routes_fail_closed_when_token_is_not_configured(monkeypatch):
    monkeypatch.delenv("SIGNAL_ENGINE_OPERATOR_API_TOKEN", raising=False)
    client = TestClient(main.app)

    response = client.post("/learning/policy/rollouts", json={})

    assert response.status_code == 503
    assert response.json()["detail"] == "operator_auth_not_configured"


def test_operator_routes_reject_missing_and_incorrect_bearer(monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_OPERATOR_API_TOKEN", "operator-secret")
    client = TestClient(main.app)

    missing = client.post("/learning/policy/rollouts", json={})
    incorrect = client.post(
        "/learning/policy/rollouts",
        headers={"Authorization": "Bearer wrong"},
        json={},
    )

    assert missing.status_code == 401
    assert incorrect.status_code == 401
    assert missing.json()["detail"] == "operator_auth_required"


def test_operator_bearer_authorizes_v2_ledger_read(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_OPERATOR_API_TOKEN", "operator-secret")
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "engine.db"))
    client = TestClient(main.app)

    response = client.get(
        "/positions/not-found/history",
        headers={"Authorization": "Bearer operator-secret"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "position_not_found"


def test_internal_write_routes_fail_closed_and_reject_wrong_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_ENGINE_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.delenv("SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN", raising=False)
    client = TestClient(main.app)

    unconfigured = client.post("/health/storage/recover", json={"dry_run": True})
    monkeypatch.setenv("SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN", "internal-secret")
    incorrect = client.post(
        "/health/storage/recover",
        headers={"X-Signal-Engine-Token": "wrong"},
        json={"dry_run": True},
    )
    authorized = client.post(
        "/health/storage/recover",
        headers={"X-Signal-Engine-Token": "internal-secret"},
        json={"dry_run": True},
    )

    assert unconfigured.status_code == 503
    assert unconfigured.json()["detail"] == "internal_write_auth_not_configured"
    assert incorrect.status_code == 403
    assert authorized.status_code == 404


def test_openapi_declares_operator_and_internal_security_schemes():
    schema = main.app.openapi()

    schemes = schema["components"]["securitySchemes"]
    assert schemes["SignalEngineOperatorBearer"]["scheme"] == "bearer"
    assert schemes["SignalEngineInternalWriteToken"]["name"] == "X-Signal-Engine-Token"
    assert {"SignalEngineOperatorBearer": []} in schema["paths"]["/learning/policy/rollouts"]["post"]["security"]
    assert {"SignalEngineInternalWriteToken": []} in schema["paths"]["/health/storage/recover"]["post"]["security"]

import importlib
import sys


def _load_listener(monkeypatch):
    monkeypatch.setenv("HELIUS_HTTPS_RPC_URL", "https://example.invalid")
    sys.modules.pop("worker.helius_listener", None)
    return importlib.import_module("worker.helius_listener")


def test_listener_helpers_tolerate_missing_transaction_shape(monkeypatch):
    listener = _load_listener(monkeypatch)
    tx = {"transaction": None, "meta": None}

    assert listener.extract_mint_from_inner_instructions(tx) is None
    assert listener.extract_new_mints_from_token_balances(tx) == []
    assert listener.extract_mints_from_token_balances(tx) == []
    assert listener.extract_buyers_from_balance_deltas(tx) == []
    assert listener._first_signer(tx) is None
    assert listener._tx_message(tx) == {}
    assert listener._tx_meta(tx) == {}


def test_program_seen_path_reads_account_keys_from_partial_tx(monkeypatch):
    listener = _load_listener(monkeypatch)
    tx = {
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": listener.PUMP_FUN_PROGRAM_ID},
                    {"pubkey": "Other1111111111111111111111111111111111111"},
                ]
            }
        },
        "meta": None,
    }

    accounts = set(
        key.get("pubkey") if isinstance(key, dict) else key
        for key in (listener._tx_message(tx).get("accountKeys") or [])
    )

    assert listener.PUMP_FUN_PROGRAM_ID in accounts


def test_mark_ws_activity_updates_global_timestamp(monkeypatch):
    listener = _load_listener(monkeypatch)
    monkeypatch.setattr(listener, "LAST_WS_ACTIVITY", 10.0)

    updated = listener._mark_ws_activity(25.0)

    assert updated == 25.0
    assert listener.LAST_WS_ACTIVITY == 25.0


def test_endpoint_logging_redacts_api_keys(monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "super-secret-key")
    listener = _load_listener(monkeypatch)
    url = "https://mainnet.helius-rpc.com/?api-key=super-secret-key"

    assert listener._endpoint_label(url) == "https://mainnet.helius-rpc.com/"
    assert listener._redact_secret_text(f"connection failed: {url}") == (
        "connection failed: https://mainnet.helius-rpc.com/?api-key=[REDACTED]"
    )


def test_ws_reconnect_delay_uses_long_floor_for_rate_limit(monkeypatch):
    listener = _load_listener(monkeypatch)
    monkeypatch.setattr(listener.random, "uniform", lambda start, end: 0.0)

    assert listener._ws_reconnect_delay("server rejected connection: HTTP 429", 1) == 60.0
    assert listener._ws_reconnect_delay("server rejected connection: HTTP 429", 7) == 120.0


def test_ws_reconnect_delay_exponentially_backs_off_other_errors(monkeypatch):
    listener = _load_listener(monkeypatch)
    monkeypatch.setattr(listener.random, "uniform", lambda start, end: 0.0)

    assert listener._ws_reconnect_delay("connection refused", 1) == 2.0
    assert listener._ws_reconnect_delay("connection refused", 2) == 4.0
    assert listener._ws_reconnect_delay("connection refused", 20) == 120.0

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

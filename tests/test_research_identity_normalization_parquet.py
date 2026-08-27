from __future__ import annotations

import json

import pytest

from research.config import load_config
from research.normalization.fees import normalize_fee
from research.normalization.trades import WSOL_MINT, classify_trade
from research.normalization.transactions import normalize_transaction
from research.parquet_writer import write_parquet_table
from research.source_adapters.dexscreener import CURRENT_DEXSCREENER_GUARD, reject_current_dexscreener_for_historical_snapshot
from research.source_adapters.solana_rpc import CURRENT_ACCOUNT_STATE_GUARD, reject_current_account_state_for_historical_snapshot


def test_transaction_normalizer_keeps_fee_payer_separate_from_buyer() -> None:
    raw = {
        "signature": "sig",
        "slot": 1,
        "timestamp": 100,
        "fee": 5000,
        "accountKeys": [{"pubkey": "payer", "signer": True}, {"pubkey": "buyer", "signer": False}],
    }
    tx = normalize_transaction(raw, token="mint", source="helius", job_id="job", request_hash="rh", response_hash="res")
    assert tx["fee_payer"] == "payer"
    assert tx["total_network_fee_lamports"] == 5000
    assert tx["fee_split_status"] == "unavailable"


def test_trade_classifier_handles_wsol_buy() -> None:
    tx = {
        "signature": "sig",
        "token": "mint",
        "success": True,
        "fee_payer": "payer",
        "signers": ["payer"],
        "pre_token_balances": [
            {"mint": "mint", "owner": "buyer", "uiTokenAmount": {"uiAmount": 0}},
            {"mint": WSOL_MINT, "owner": "buyer", "uiTokenAmount": {"uiAmount": 2}},
        ],
        "post_token_balances": [
            {"mint": "mint", "owner": "buyer", "uiTokenAmount": {"uiAmount": 100}},
            {"mint": WSOL_MINT, "owner": "buyer", "uiTokenAmount": {"uiAmount": 1}},
        ],
    }
    trade = classify_trade(tx, token="mint")
    assert trade["side"] == "buy"
    assert trade["trader"] == "buyer"
    assert trade["fee_payer"] == "payer"


def test_fee_normalizer_preserves_unavailable_split() -> None:
    fee = normalize_fee({"signature": "sig", "total_network_fee_lamports": 10, "fee_payer": "payer"})
    assert fee["total_network_fee_lamports"] == 10
    assert fee["priority_fee_lamports"] is None


def test_current_state_guards_for_historical_snapshots() -> None:
    with pytest.raises(ValueError, match=CURRENT_ACCOUNT_STATE_GUARD):
        reject_current_account_state_for_historical_snapshot(1, 2)
    with pytest.raises(ValueError, match=CURRENT_DEXSCREENER_GUARD):
        reject_current_dexscreener_for_historical_snapshot(1, 10_000)


def test_parquet_writer_atomic_and_readable(tmp_path) -> None:
    pyarrow = pytest.importorskip("pyarrow.parquet")
    config = load_config(db_path=str(tmp_path / "r.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    result = write_parquet_table(config, "normalized_transactions", [{"row_id": "a", "chain": "solana", "token": "mint", "observed_at": 100, "warnings": []}], token="mint")
    assert result["row_count"] == 1
    table = pyarrow.read_table(result["path"])
    assert table.num_rows == 1


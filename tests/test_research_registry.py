from __future__ import annotations

from research.config import load_config
from research.registry import detect_chain, load_operator_seed_addresses, validate_operator_seeds


def test_operator_seed_addresses_are_preserved_exactly() -> None:
    addresses = load_operator_seed_addresses()
    assert addresses == [
        "CTPoyCwkjMvoJwU4xvZZqoD8tiYk6yDchySiN5gGpump",
        "Ai66LHZG9MCzg1WKdawwqduVAXpNDUuV8M3uyq5ppump",
        "6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx",
        "0xb75d5ee14708e7efbea939311090061d72265608",
        "A13oRB9FFaiUjfi6LdCg6p9ka1u8SfGkUFs4SKvPpump",
        "0xddfed493a114d610c5709fefd22baef40dc23428",
        "FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2",
        "zj1jpp7QMveWHLs61vL9KMZf254KvW7j4AAmBF8ry2k",
        "GUmbtfjSZkybSFgPibBcvwExEBdXwewJHR5PkTjzpump",
        "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump",
        "Ge87EtsjwRQbHaqQmKRno69RFTwh9bfSsm99XNxTpump",
    ]


def test_chain_detection_separates_solana_and_evm() -> None:
    assert detect_chain("CTPoyCwkjMvoJwU4xvZZqoD8tiYk6yDchySiN5gGpump") == "solana"
    assert detect_chain("0xb75d5ee14708e7efbea939311090061d72265608") == "evm"
    assert detect_chain("not a mint") == "invalid"


def test_validate_operator_seeds_keeps_operator_label_distinct(tmp_path) -> None:
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    result = validate_operator_seeds(config)
    assert result["count"] == 11
    assert result["solana_count"] == 9
    assert result["evm_count"] == 2
    assert result["duplicates"] == []
    assert {item["verification_status"] for item in result["results"]} == {"pending"}
    assert {item["operator_outcome_label"] for item in result["results"]} == {"recent_winner"}
    assert all(item["canonical_symbol"] is None for item in result["results"])


from __future__ import annotations

import json

from research.capabilities import probe_source_capabilities
from research.config import load_config


def test_capability_probe_records_missing_keys_without_secrets(tmp_path, monkeypatch) -> None:
    for key in ("BIRDEYE_API_KEY", "HELIUS_API_KEY", "JUPITER_API_KEY", "HELIUS_RPC_URL"):
        monkeypatch.delenv(key, raising=False)
    config = load_config(db_path=str(tmp_path / "research.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    result = probe_source_capabilities(config)
    by_source = {item["source"]: item for item in result["sources"]}
    assert by_source["birdeye"]["unavailable_reason"] == "missing_env:BIRDEYE_API_KEY"
    assert by_source["dexscreener"]["enabled"] is True
    payload = json.loads((tmp_path / "artifacts" / "source_capabilities.json").read_text())
    assert "API_KEY" not in json.dumps(payload).replace("BIRDEYE_API_KEY", "").replace("HELIUS_API_KEY", "").replace("JUPITER_API_KEY", "")


from app.services import wallet_service


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_wallet_risk_score_normalizes_largest_accounts_by_token_supply(monkeypatch):
    monkeypatch.setattr(wallet_service, "HELIUS_API_KEY", "key")
    monkeypatch.setattr(wallet_service, "HELIUS_RPC_URL", "")
    wallet_service._CACHE.clear()

    def _post(_url, json, timeout):
        if json["method"] == "getTokenLargestAccounts":
            return _Response(
                {
                    "result": {
                        "value": [
                            {"uiAmount": 120_000},
                            {"uiAmount": 80_000},
                            {"uiAmount": 50_000},
                        ]
                    }
                }
            )
        if json["method"] == "getTokenSupply":
            return _Response({"result": {"value": {"uiAmount": 1_000_000}}})
        raise AssertionError(json["method"])

    monkeypatch.setattr(wallet_service.requests, "post", _post)

    result = wallet_service.wallet_risk_score("token")

    assert result["status"] == "computed"
    assert result["top_holder_pct"] == 0.12
    assert result["top10_pct"] == 0.25
    assert result["risk"] == "high"


def test_wallet_risk_score_requires_supply_denominator(monkeypatch):
    monkeypatch.setattr(wallet_service, "HELIUS_API_KEY", "key")
    monkeypatch.setattr(wallet_service, "HELIUS_RPC_URL", "")
    wallet_service._CACHE.clear()

    def _post(_url, json, timeout):
        if json["method"] == "getTokenLargestAccounts":
            return _Response({"result": {"value": [{"uiAmount": 120_000}]}})
        if json["method"] == "getTokenSupply":
            return _Response({"result": {"value": {}}})
        raise AssertionError(json["method"])

    monkeypatch.setattr(wallet_service.requests, "post", _post)

    result = wallet_service.wallet_risk_score("token")

    assert result["status"] == "insufficient_data"
    assert result["top_holder_pct"] is None
    assert result["reason"] == "no_holder_supply_data"

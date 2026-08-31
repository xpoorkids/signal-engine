import pytest

from app.services import dex_service


@pytest.fixture(autouse=True)
def _reset_dex_provider_runtime_state():
    dex_service._PROVIDER_COOLDOWN_UNTIL_TS = 0.0
    dex_service._PROVIDER_COOLDOWN_REASON = None
    dex_service._PROVIDER_CONSECUTIVE_FAILURES = 0
    dex_service._PROVIDER_SUPPRESSED_SCAN_COUNT = 0
    yield
    dex_service._PROVIDER_COOLDOWN_UNTIL_TS = 0.0


def test_fetch_solana_pairs_exposes_scan_lifecycle(monkeypatch):
    def fake_search():
        health = {
            "search:test": dex_service._source_item(
                "search:test",
                ok=True,
                pair_count=1,
            )
        }
        assert dex_service.LAST_SOURCE_HEALTH["in_progress"] is True
        assert dex_service.LAST_SOURCE_HEALTH["current_source"] == "search"
        return ([{"pairAddress": "pair-1"}], health)

    monkeypatch.setattr(dex_service, "_fetch_search_pairs", fake_search)
    monkeypatch.setattr(dex_service, "_fetch_profile_pairs", lambda: ([], {}))
    monkeypatch.setattr(dex_service, "_fetch_external_seed_pairs", lambda: ([], {}))
    monkeypatch.setattr(dex_service, "_fetch_j7tracker_pairs", lambda: ([], {}))

    pairs = dex_service.fetch_solana_pairs()
    health = dex_service.get_dex_source_health()

    assert pairs == [{"pairAddress": "pair-1"}]
    assert health["in_progress"] is False
    assert health["current_source"] is None
    assert health["last_started_ts"] is not None
    assert health["last_finished_ts"] is not None
    assert health["total_pairs"] == 1
    assert health["sources"]["search:test"]["pair_count"] == 1


def test_dex_provider_honors_retry_after_and_suppresses_followup_scan(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 429
        headers = {"Retry-After": "240"}

    def fake_get(*_args, **_kwargs):
        calls.append(True)
        return FakeResponse()

    monkeypatch.setattr(dex_service.requests, "get", fake_get)

    with pytest.raises(dex_service.DexProviderCooldown):
        dex_service._fetch_json("https://api.dexscreener.com/test")
    pairs = dex_service.fetch_solana_pairs()
    health = dex_service.get_dex_source_health()

    assert pairs == []
    assert len(calls) == 1
    assert health["cooldown_reason"] == "rate_limited_http_429"
    assert health["cooldown_remaining_seconds"] > 200
    assert health["suppressed_scan_count"] == 1

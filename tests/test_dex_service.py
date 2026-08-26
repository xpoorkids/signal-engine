from app.services import dex_service


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

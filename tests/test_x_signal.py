from worker import x_signal


def test_build_query_always_includes_token_when_template_omits_it(monkeypatch):
    monkeypatch.setattr(x_signal, "X_QUERY_TEMPLATE", '"{name}" OR "${symbol}"')

    query = x_signal._build_query("TokenMint123", "ABC", "Alpha Beta")

    assert "TokenMint123" in query
    assert "Alpha Beta" in query
    assert "$ABC" in query


def test_fetch_x_signal_counts_heavy_and_verified_authors(monkeypatch):
    monkeypatch.setattr(x_signal, "X_BEARER_TOKEN", "token")
    monkeypatch.setattr(x_signal, "X_HEAVY_HANDLES", {"heavyhandle"})
    monkeypatch.setattr(x_signal, "X_HEAVY_AUTHOR_IDS", {"42"})
    x_signal._CACHE.clear()

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "data": [
                    {"author_id": "42", "public_metrics": {"like_count": 5, "retweet_count": 2, "reply_count": 1}},
                    {"author_id": "7", "public_metrics": {"like_count": 3, "retweet_count": 1, "reply_count": 0}},
                ],
                "includes": {
                    "users": [
                        {
                            "id": "42",
                            "username": "HeavyHandle",
                            "verified": True,
                            "public_metrics": {"followers_count": 60000},
                        },
                        {
                            "id": "7",
                            "username": "small",
                            "verified": True,
                            "public_metrics": {"followers_count": 1000},
                        },
                    ]
                },
            }

    def fake_get(*_args, **_kwargs):
        return FakeResponse()

    monkeypatch.setattr(x_signal.requests, "get", fake_get)

    result = x_signal.fetch_x_signal("TokenMint123", "ABC", "Alpha Beta")

    assert result["tweet_count"] == 2
    assert result["unique_authors"] == 2
    assert result["heavy_author_count"] == 1
    assert result["verified_author_count"] == 2
    assert result["author_followers"] == 61000
    health = x_signal.get_x_signal_health()
    assert health["configured"] is True
    assert health["last_token"] == "TokenMint123"
    assert health["last_status_code"] == 200
    assert health["last_result_count"] == 2

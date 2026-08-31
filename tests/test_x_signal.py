import pytest

from worker import attention, x_signal


@pytest.fixture(autouse=True)
def _reset_x_signal_runtime_state():
    x_signal._CACHE.clear()
    x_signal._COOLDOWN_UNTIL_TS = 0.0
    x_signal._COOLDOWN_REASON = None
    x_signal._CONSECUTIVE_FAILURES = 0
    x_signal._SUPPRESSED_QUERY_COUNT = 0
    yield
    x_signal._COOLDOWN_UNTIL_TS = 0.0


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


def test_fetch_x_signal_cools_down_after_authorization_failure(monkeypatch):
    monkeypatch.setattr(x_signal, "X_BEARER_TOKEN", "invalid-token")
    monkeypatch.setenv("SIGNAL_ENGINE_X_AUTH_COOLDOWN_SEC", "600")
    calls = []

    class FakeResponse:
        status_code = 401
        headers = {}

    def fake_get(*_args, **_kwargs):
        calls.append(True)
        return FakeResponse()

    monkeypatch.setattr(x_signal.requests, "get", fake_get)

    assert x_signal.fetch_x_signal("TokenMint123", "ABC", "Alpha Beta") is None
    assert x_signal.fetch_x_signal("OtherTokenMint", "DEF", "Delta") is None

    health = x_signal.get_x_signal_health()
    assert len(calls) == 1
    assert health["cooldown_reason"] == "auth_http_401"
    assert health["cooldown_remaining_seconds"] > 0
    assert health["suppressed_query_count"] == 1


def test_viral_theme_hits_detect_animals_and_events():
    class Event:
        extra = {"symbol": "DOG2026", "name": "Election Dog"}

    hits, categories = attention._viral_theme_hits(Event())

    assert "dog" in hits
    assert "election" in hits
    assert "animal" in categories
    assert "event" in categories

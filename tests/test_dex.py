from worker.dex import summarize_pair
from app.services import dex_service


def test_summarize_pair_preserves_h24_volume_and_transactions():
    summary = summarize_pair(
        {
            "pairAddress": "pair-1",
            "dexId": "raydium",
            "priceUsd": "0.01",
            "liquidity": {"usd": 50000},
            "volume": {"m5": 1000, "h1": 12000, "h6": 50000, "h24": 150000},
            "txns": {
                "m5": {"buys": 10, "sells": 4},
                "h1": {"buys": 80, "sells": 40},
                "h24": {"buys": 900, "sells": 500},
            },
            "priceChange": {"m5": 4.0, "h1": 22.0, "h24": 180.0},
            "fdv": 200000,
            "marketCap": 180000,
        }
    )

    assert summary["volume_h24"] == 150000
    assert summary["volume_h6"] == 50000
    assert summary["txns_h1_buys"] == 80
    assert summary["txns_h24_sells"] == 500


def test_external_seed_pairs_fetches_configured_tokens(monkeypatch):
    token_a = "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump"
    token_b = "DdPrHYqM8Ueovnk9kAnAgoGhswkuaTqmxcoZzU3Zpump"
    monkeypatch.setenv(
        "SIGNAL_ENGINE_EXTERNAL_SEED_TOKENS",
        f"{token_a}, {token_b}, {token_a}",
    )
    fetched_urls = []

    def fake_fetch_json(url: str):
        fetched_urls.append(url)
        return {
            "pairs": [
                {"chainId": "solana", "pairAddress": "pair-a"},
                {"chainId": "solana", "pairAddress": "pair-a"},
                {"chainId": "solana", "pairAddress": "pair-b"},
            ]
        }

    monkeypatch.setattr(dex_service, "_fetch_json", fake_fetch_json)

    pairs = dex_service._fetch_external_seed_pairs()

    assert f"{token_a},{token_b}" in fetched_urls[0]
    assert [item["pairAddress"] for item in pairs] == ["pair-a", "pair-b"]
    assert all(item["signal_engine_source"] == "external_seed" for item in pairs)


def test_profile_pairs_preserve_discovery_sources(monkeypatch):
    token = "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump"
    responses = {
        "https://api.dexscreener.com/token-profiles/latest/v1": [
            {"chainId": "solana", "tokenAddress": token}
        ],
        "https://api.dexscreener.com/community-takeovers/latest/v1": [
            {"chainId": "solana", "url": f"https://pump.fun/coin/{token}"}
        ],
        "https://api.dexscreener.com/ads/latest/v1": [],
        "https://api.dexscreener.com/token-boosts/latest/v1": [],
        "https://api.dexscreener.com/token-boosts/top/v1": [],
    }

    def fake_fetch_json(url: str):
        if url in responses:
            return responses[url]
        return {
            "pairs": [
                {
                    "chainId": "solana",
                    "pairAddress": "pair-a",
                    "baseToken": {"address": token},
                    "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
                }
            ]
        }

    monkeypatch.setattr(dex_service, "_fetch_json", fake_fetch_json)

    pairs = dex_service._fetch_profile_pairs()

    assert pairs[0]["signal_engine_sources"] == ["community_takeover", "token_profile"]

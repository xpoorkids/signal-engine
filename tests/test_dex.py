from worker.dex import summarize_pair


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

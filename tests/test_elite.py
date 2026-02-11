from worker.elite import EliteTracker


def test_elite_score_basic():
    tracker = EliteTracker()
    token = "TEST"
    elite = tracker.compute_elite_score(
        token=token,
        buy_size_sol=1.2,
        unique_10s=3,
        total_buys_30s=3,
        unique_wallets_30s=3,
        top_wallet_share=0.34,
        liq_usd=60000,
        liq_locked=True,
        hard_fail=False,
    )
    assert elite >= 8


def test_elite_score_hard_fail():
    tracker = EliteTracker()
    token = "TEST"
    elite = tracker.compute_elite_score(
        token=token,
        buy_size_sol=3.0,
        unique_10s=5,
        total_buys_30s=5,
        unique_wallets_30s=5,
        top_wallet_share=0.2,
        liq_usd=100000,
        liq_locked=True,
        hard_fail=True,
    )
    assert elite == -999

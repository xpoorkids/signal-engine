from worker.attention import burst_weight_from_sol


def test_burst_weight_from_sol_preserves_default_buckets():
    assert burst_weight_from_sol(0.05) == 1
    assert burst_weight_from_sol(0.5) == 2
    assert burst_weight_from_sol(1.5) == 3
    assert burst_weight_from_sol(3.5) == 5

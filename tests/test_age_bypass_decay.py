import time
from worker.elite import EliteTracker


def test_age_bypass_ttl_behavior():
    tracker = EliteTracker()
    token = "TEST"
    st = tracker.get_state(token)
    now = time.monotonic()
    st.age_bypass_until = now + 20
    assert st.age_bypass_until > now


def test_momentum_decay_blacklist():
    tracker = EliteTracker()
    token = "TEST"
    st = tracker.get_state(token)
    st.decay_watch_started = time.monotonic()
    st.decay_start_unique_5m = 5
    st.decay_start_burst_10s = 10
    st.decay_start_liq = 10000
    ttl = tracker.update_decay(token, attention=0.4, burst_10s=3, unique_buyers_5m=5, liq_usd=7000)
    assert ttl == 600

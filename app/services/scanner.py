from datetime import datetime

def run_scan():
    """
    V1 scanner:
    - Returns a small, deterministic list
    - No external APIs yet
    - Shape is FINAL (important)
    """

    now = datetime.utcnow().isoformat()

    return [
        {
            "symbol": "TEST",
            "chain": "sol",
            "reason": "stub_signal",
            "observed_at": now,
            "metrics": {
                "social_velocity": 0,
                "volume_delta": 0,
                "liquidity_usd": 0
            }
        }
    ]

CONF_WEIGHTS = {
    "logs_initialize_mint": 0.25,
    "tx_pump_observed": 0.25,
    "token_resolved": 0.30,
    "dex_pair_found": 0.35,
    "wallet_low_risk": 0.20,
    "repeat": 0.05,
}

CAPS = {
    "early": 0.49,
    "heating": 0.79,
    "promoted": 0.99,
}


def bump(conf: float, delta: float, cap: float) -> float:
    return min(cap, max(conf, 0.0) + delta)

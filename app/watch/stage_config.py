SOL_STAGE_THRESHOLDS = {
    "lp_usd": {"min": 12_000, "mid": 30_000, "high": 60_000},
    "vol_5m": {"low": 3_000, "mid": 10_000, "high": 25_000},
    "tx_5m": {"low": 18, "mid": 50, "high": 120},
    "holders_delta_15m": {"low": 20, "mid": 50, "high": 120},
    "top10_pct": {"warn": 40, "bad": 50, "severe": 65},
    "stage_cutoffs": {"building": 5, "near_pass": 10},
    "near_pass_confirmation": {
        "min_consecutive": 2,
        "trend_window": 3,
        "min_slope": 0.0,
        "promotion_cooldown_ticks": 2,
        "demote_cutoff": 8,
        "history_size": 20,
    },
}

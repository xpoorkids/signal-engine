# Outcome Labels

Outcome labels must be produced from immutable feature snapshots containing only information available at decision time.

## Target Horizons

`15s`, `30s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`, `24h`

## Target Labels

- `p_up_25_before_down_15_15m`
- `p_up_50_before_down_20_60m`
- `p_up_100_before_down_30_4h`
- `p_positive_net_return_15m`
- `p_positive_net_return_60m`
- `p_liquidity_failure_60m`
- `p_sell_route_failure_60m`
- `p_rug_like_event_4h`

## Quality Flags

- `executable_quote`
- `reference_price_only`
- `unresolved`
- `insufficient_observation_time`
- `source_failure`
- `token_disappeared`
- `no_route`
- `missing_quote`
- `explicit_negative_event`

Reference-price-only results must not be mixed with executable P&L.

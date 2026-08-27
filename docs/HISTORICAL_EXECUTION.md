# Historical Execution Reconstruction

Historical execution is separate from chart return.

## Evidence Order

1. Historical executable quote observed at the time.
2. Historical pool reserves and venue AMM math.
3. Nearby actual swaps at comparable size.
4. Historical pair liquidity with a validated impact model.
5. Reference price only.

## Quality Labels

- `historical_quote_observed`
- `historical_reserve_reconstructed`
- `historical_trade_inferred`
- `historical_liquidity_estimated`
- `reference_price_only`
- `no_route`
- `insufficient_data`

## Jupiter Guard

The runtime guard `current_jupiter_quote_cannot_be_used_as_historical_quote` rejects present-day Jupiter quotes for historical timestamps. Current Jupiter data may validate current behavior, but cannot prove a past executable route.


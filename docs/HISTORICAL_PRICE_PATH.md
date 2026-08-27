# Historical Price Path

Source snapshots select price evidence in this order:

1. normalized trade execution price
2. Birdeye OHLCV close for a completed candle
3. reserve reconstruction when implemented for the venue
4. reference-only historical value

Current DEX Screener price and current Jupiter price are excluded from historical snapshots. Each selected point records source and quality: `trade_observed`, `ohlcv_observed`, `reserve_reconstructed`, `reference_only`, or `missing`.

# Birdeye Market History

Source mode uses endpoint-specific Birdeye clients:

- `GET /defi/token_creation_info`
- `GET /defi/v3/ohlcv`
- `GET /defi/v3/token/txs`
- current-only token overview, security, and holder endpoints

OHLCV requests use deterministic segmentation with a 5,000-candle maximum per request. The collector starts with the finest configured interval and downgrades when the request returns no usable rows or falls outside retention.

Candles are written to `market_candles` with the source interval preserved. Missing candles are not padded. A 1-minute candle is never labeled as second-level evidence.

Token trades are paginated by offset and written as source-parsed trade rows. On-chain classifications and Birdeye parsed trades can coexist and are reconciled later instead of overwriting each other.

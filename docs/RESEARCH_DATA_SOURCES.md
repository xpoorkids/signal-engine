# Research Data Sources

The research CLI probes source capabilities at the start of each run and writes `artifacts/research/source_capabilities.json`.

## Sources

- Birdeye: token identity, OHLCV, trades, liquidity, holders, security, token fees where the configured plan permits.
- Helius: transaction history, block time, fee payer, network fees, token balance changes, and account context.
- DEX Screener: pairs, pair metadata, boosts, ads, profiles, and community-takeover indicators where documented.
- Jupiter: current validation only. Present-day quotes are forbidden as historical execution evidence.
- Solana RPC: fallback for signatures, transactions, mint information, account ownership, supply, authorities, and fee data.

## Retention

Different sources retain different granularities for different periods. The research system records requested coverage, returned coverage, and whether a field is direct, inferred, outside retention, or unsupported by the current plan.

Missing historical data is never converted to zero.


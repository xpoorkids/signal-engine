# Real Historical Pilot Gap Audit

Implementation date: 2026-08-27

Parser versions:

- `helius-history-adapter-v1`
- `solana-rpc-adapter-v1`
- `birdeye-history-adapter-v1`
- `dexscreener-research-adapter-v1`
- `source-historical-snapshot-v1`
- `source-historical-outcome-v1`

## Current Source-Mode Call Graph

`research.cli` routes source-mode build commands into `research.source_pipeline`.

- `plan-backfill --mode source` probes capabilities and estimates source operations.
- `backfill --mode source` validates source identity, collects source pages, caches raw responses, normalizes rows, and writes Parquet.
- `build-features --mode source` reads source Parquet rows and builds timestamped point-in-time snapshots.
- `build-outcomes --mode source` reads the historical price/liquidity path and writes source outcomes.
- `replay-actions --mode source` reuses `ActionEngineService` through a strict historical adapter.
- `report --mode source` writes `artifacts/research/real_historical_pilot`.

Source pipeline boundaries assert `fixture_data_used == false`; source reports fail if fixture rows appear in source totals.

## Source Documentation Checked

- Helius `getTransactionsForAddress`: current docs describe the Helius RPC method as supporting full transaction data, ascending chronological sort, filters, `paginationToken`, and up to 1,000 full transactions per call.
- Solana RPC `getSignaturesForAddress`: current docs return newest-first signature metadata with `before` and `until`.
- Solana RPC `getTransaction`: current docs return a transaction by signature or `null`, with object-form config including `encoding` and `maxSupportedTransactionVersion`.
- Birdeye OHLCV V3: current docs list `GET /defi/v3/ohlcv`, intervals including `1s`, `15s`, `30s`, max 5,000 records, no padding by default, and shorter retention for fine intervals.
- Birdeye token trades: current docs list `GET /defi/v3/token/txs` for token-level trade history.
- DEX Screener API: current docs list token pairs, pair lookup, latest profiles, latest boosts, top boosts, and orders endpoints. Most returned values are current or recent context, not historical state.

## Previous Gaps

- Helius used the legacy enhanced REST endpoint one page at a time.
- Solana RPC retrieved signatures but did not hydrate every signature.
- Birdeye OHLCV and token trades were adapter methods but not source-pipeline Parquet outputs.
- Historical liquidity was not reconstructed or explicitly separated from current DEX Screener liquidity.
- Source snapshots used `snapshot_ts = 0`.
- Source outcomes were always `insufficient_data`.
- Real controls were blocked without a source-backed candidate-pool attempt.
- Action replay could run structurally but was not forced to treat missing history as missing.

## Implemented Gap Closure

- Helius now uses the documented RPC `getTransactionsForAddress` with `transactionDetails=full`, `sortOrder=asc`, `paginationToken`, finality, timestamp/slot filters, token account filtering, page limits, record limits, request-budget stops, and page-level hash metadata.
- Solana RPC now paginates `getSignaturesForAddress` with `before`/`until` and hydrates signatures through `getTransaction`; null or unavailable results are retained with hydration states.
- Helius and RPC rows are reconciled into `transaction_source_reconciliation`.
- Birdeye source backfill now writes V3 OHLCV rows to `market_candles` and V3 token transactions to `normalized_trades`.
- Historical liquidity writes only when a historical source row includes a liquidity field; current DEX Screener liquidity remains current-only.
- Source snapshots are timestamped from creation, earliest activity, transactions, trades, or candles and reject current-only holder/DEX/Jupiter inputs from historical replay.
- Outcomes are calculated from historical price paths when two or more price points exist; otherwise they remain explicitly insufficient.
- Strict replay records `insufficient_evidence` instead of passing optimistic defaults into the action engine.

## Remaining Limitations

- The local environment used for this implementation did not contain `HELIUS_API_KEY`, `HELIUS_RPC_URL`, or `BIRDEYE_API_KEY`, so the live proof-token historical run is blocked by missing credentials.
- DEX Screener can provide public current context, pair creation, boosts, profiles, and paid-order evidence, but current price/liquidity is excluded from historical snapshots.
- Holder history remains unavailable unless a direct historical holder source or complete transfer reconstruction is present.
- Fee authenticity metrics remain incomplete when wallet-cluster coverage is missing; fee payer and trader identities are still kept separate.
- Real matched controls require historical creation windows from Helius/RPC/Birdeye before controls can be selected without outcome leakage.

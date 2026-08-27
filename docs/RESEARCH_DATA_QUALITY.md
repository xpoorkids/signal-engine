# Research Data Quality

Every feature should retain a state:

- `computed`
- `missing`
- `unavailable`
- `stale`
- `insufficient_history`
- `outside_source_retention`
- `unsupported_by_current_api_plan`
- `reference_price_only`
- `executable_estimate`
- `inferred`
- `directly_observed`

Per-token coverage uses:

- `complete`
- `usable`
- `partial`
- `weak`
- `unavailable`
- `outside_retention`

Tokens with materially different evidence quality must remain distinguishable in reports.

Source-mode reports also distinguish real source-backed, reconstructed, current-only, reference-only, missing, and fixture data. Fixture rows are excluded from source-pilot totals.
# Real Historical Pilot Quality Rules

Source-mode research distinguishes:

- direct source-backed
- cross-source confirmed
- reconstructed
- inferred
- current-only
- reference-only
- missing
- outside retention
- fixture

Fixture rows must be zero in source totals. Current DEX Screener liquidity, current Jupiter quotes, and current holder distribution are retained only as current context and are excluded from historical snapshots and strict action replay.

Historical source rows must retain source, operation, observed time, fetched time, request hash, response hash, parser version, data mode, completeness, and warnings.

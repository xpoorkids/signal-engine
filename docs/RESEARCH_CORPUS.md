# Solana Memecoin Research Corpus V1

This package builds a separate offline research system for historical Solana memecoin analysis. It is not part of the production worker and does not alter live thresholds, Discord routing, or execution behavior.

## Scope

- Preserve operator-supplied examples with their original addresses.
- Separate Solana mints from EVM contracts before Solana analysis.
- Build source capability evidence before running backfills.
- Cache raw responses with non-secret provenance.
- Construct point-in-time snapshots without future leakage.
- Compare verified runners with matched controls.
- Replay the current action engine chronologically.

## Storage

Research storage is separate from `engine.db`:

- `SIGNAL_ENGINE_RESEARCH_DB_PATH`, default `state/research.db`
- `SIGNAL_ENGINE_RESEARCH_DATA_DIR`, default `research_data`
- `SIGNAL_ENGINE_RESEARCH_ARTIFACT_DIR`, default `artifacts/research`

Large raw responses, research databases, Parquet exports, and cache files are git-ignored.

## Current Pilot

The first implementation includes an offline fixture pilot so the pipeline can be tested without paid historical APIs. Fixture outputs are labeled `fixture_only` and must not be used for threshold tuning or profitability claims.


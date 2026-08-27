# Research Reproduction

Run the offline research workflow from the repository root:

```bash
python -m research.cli capabilities
python -m research.cli validate-seeds
python -m research.cli backfill --cohort operator_seed_cohort_v1
python -m research.cli build-features
python -m research.cli build-outcomes
python -m research.cli build-controls
python -m research.cli replay-actions
python -m research.cli report
python -m research.cli status
```

The default pilot works without source credentials and labels generated rows `fixture_only`. Real historical research requires configured source APIs and source-specific retention probes.

Do not commit research databases, raw API caches, large Parquet exports, wallet caches, or API response dumps.


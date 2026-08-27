# Research Reproduction

Run the offline research workflow from the repository root:

```bash
python -m research.cli capabilities --mode fixture
python -m research.cli validate-seeds --mode fixture
python -m research.cli backfill --mode fixture --cohort operator_seed_cohort_v1
python -m research.cli build-features --mode fixture
python -m research.cli build-outcomes --mode fixture
python -m research.cli build-controls --mode fixture
python -m research.cli replay-actions --mode fixture
python -m research.cli report --mode fixture
python -m research.cli status --mode fixture
```

The default pilot works without source credentials and labels generated rows `fixture_only`. Real historical research requires configured source APIs and source-specific retention probes.

Source-mode proof runs require explicit mode:

```bash
python -m research.cli doctor --mode source
python -m research.cli plan-backfill --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
```

Do not commit research databases, raw API caches, large Parquet exports, wallet caches, or API response dumps.

# Research Parquet Schemas

Source-backed rows are written under:

```text
research_data/parquet/{table}/chain=solana/token_prefix={prefix}/year={yyyy}/month={mm}/
```

Current tables include:

- `token_identity`
- `pair_observations`
- `normalized_transactions`
- `normalized_trades`
- `transaction_fees`

Every row should include chain, token, source, source operation, observed time, fetched time, evidence quality, parser version, job ID, request hash, response hash, data mode, completeness, and warnings where available.

Writes use a temporary file and atomic rename.


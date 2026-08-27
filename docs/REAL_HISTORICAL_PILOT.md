# Real Historical Pilot

Proof token:

`FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2`

Reproduce the bounded proof run:

```bash
python -m research.cli doctor --mode source
python -m research.cli capabilities --mode source
python -m research.cli plan-backfill --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2 --source helius,solana_rpc,birdeye,dexscreener
python -m research.cli backfill --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2 --source helius,solana_rpc,birdeye,dexscreener --request-budget 100 --max-pages 3 --max-records 250 --concurrency 2 --resume
python -m research.cli build-features --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
python -m research.cli build-outcomes --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
python -m research.cli replay-actions --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
python -m research.cli report --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
```

The report is written to `artifacts/research/real_historical_pilot`.

Completion states:

- `source_pilot_complete`
- `source_pilot_partial`
- `blocked_by_retention`
- `blocked_by_plan`
- `blocked_by_missing_credentials`
- `blocked_by_unavailable_history`
- `failed`

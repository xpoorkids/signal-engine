# Source Research Pilot

The proof token is:

```text
FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
```

Run:

```bash
python -m research.cli capabilities --mode source
python -m research.cli doctor --mode source
python -m research.cli plan-backfill --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
python -m research.cli backfill --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2 --resume
python -m research.cli build-features --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
python -m research.cli build-outcomes --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
python -m research.cli replay-actions --mode source --token FZqdw6oSDCbHtKYxmhnfbi97SnyVy8jaYpdCoMrrjKa2
python -m research.cli report --mode source
```

The source pilot report is written to `artifacts/research/source_pilot/`. If required credentials or endpoint plans are missing, the proof remains blocked or partial and fixture data is not counted.


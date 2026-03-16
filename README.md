# Signal Engine

Production-oriented crypto signal engine for Solana token discovery, scoring, alerting, and post-alert learning.

## What It Does

- Ingests token activity from Helius and Dexscreener-derived enrichment paths.
- Scores tokens with deterministic attention, risk, execution, creator, and promotion logic.
- Publishes elite Discord alerts for candidate, watch, breakout, and risk-alert states.
- Persists signal outcomes and generates learning reports without turning the engine into a black box.

## System Shape

### Core pipeline

1. `worker.helius_listener` emits raw token/buy events.
2. `worker.promote.process_event` enriches and scores events.
3. `worker.runner` sends candidate/promoted/heating alerts to Discord.
4. `app.services.signal_learning_service` records emitted signals and schedules post-alert snapshots.
5. Daily learning reports summarize which setups and sessions performed best or worst.

### Major subsystems

- `worker/`
  Runtime scoring, enrichment, promotion, Discord formatting, and alert dispatch.
- `app/services/`
  State, review APIs, presentation contracts, learning persistence, and HTTP-facing services.
- `app/routes/`
  FastAPI routes for health, scan, score, review, watch summaries, and learning reports.
- `state/engine.db`
  SQLite database for engine state, candidate state, wallet state, and learning tables.

## New Learning Layer

The engine remains deterministic. The learning layer observes outcomes around emitted signals.

Tables added:

- `signals`
- `signal_snapshot_jobs`
- `signal_snapshots`
- `learning_reports`

Features:

- records every emitted candidate/heating/promoted alert
- attaches session/daypart features
- captures snapshots at `+5m`, `+15m`, `+1h`, `+4h`
- classifies outcomes like `worked`, `failed`, `faded`, `strong_continuation`
- generates daily learning summaries from real outcomes

## HTTP Routes

- `GET /health`
- `POST /scan`
- `POST /score`
- `GET /packet/{symbol}`
- `GET /watch/summary`
- `GET /review/{token}`
- `POST /review`
- `GET /learning/report/latest`
- `GET /learning/report/{report_date}`
- `GET /learning/tuning/proposals`
- `GET /learning/tuning/proposals/dashboard`
- `GET /learning/tuning/proposals/env`
- `GET /learning/tuning/proposals/diff`
- `GET /learning/tuning/profiles`
- `GET /learning/tuning/profiles/dashboard`
- `POST /learning/tuning/approvals`
- `GET /learning/tuning/approvals`
- `GET /learning/tuning/approvals/dashboard`
- `POST /learning/tuning/approvals/{approval_id}/status`
- `GET /learning/tuning/approvals/latest`
- `GET /learning/tuning/approvals/latest/artifact`
- `GET /learning/tuning/approvals/latest/bundle`
- `GET /learning/tuning/drift`
- `GET /learning/tuning/rollout/summary`
- `GET /learning/tuning/rollout/dashboard`
- `GET /learning/tuning/notifications`
- `GET /learning/tuning/notifications/dashboard`
- `GET /learning/command-center`
- `GET /learning/command-center/dashboard`
- `GET /learning/ops/digest`
- `GET /learning/ops/digest/dashboard`
- `GET /learning/ops/digest/text`
- `POST /learning/ops/digest/send`

## Running Locally

## Tuning Rollout Flow

1. Create a tuning approval. New approvals start in `pending`.
2. Promote to `approved` after review.
3. Mark as `rolled_out` with deployment metadata:
   - `deployment_service`
   - `deployment_sha`
   - `deployment_env`
   If omitted and available, these can default from environment variables such as `SIGNAL_ENGINE_DEPLOY_*` or Render metadata.
   Required aligned profiles can be enforced with `SIGNAL_ENGINE_REQUIRED_ALIGNED_PROFILES`.
   Use `allow_misaligned=true` on the status transition only when you intentionally need to bypass that guardrail.

### Requirements

- Python 3.11 preferred
- environment variables configured in `.env`

### Install

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pytest
```

### Run API

```bash
uvicorn app.main:app --reload
```

### Run worker

```bash
python -m worker.runner
```

## Validation

### Syntax check

```bash
python -m compileall app worker tests ocr_token_alert_parser.py
```

### Unit tests

```bash
pytest -q tests/test_signal_metrics.py tests/test_signal_learning_service.py tests/test_learning_route.py
```

### Deterministic scan tests

```bash
python -m tests.scan_test
python -m tests.scan_replay_test
```

## Design Rules

- scoring remains explicit and reviewable
- missing metrics never silently render as fake zeroes
- Discord/UI presentation should reflect real computed values only
- learning suggests improvements; it does not auto-rewrite the engine

## Immediate Next Improvements

- expose learning report summaries directly in Discord/admin surfaces
- add richer regime analytics by weekday/session/lifecycle/risk bucket
- add promotion outcome comparisons versus candidate-only alerts
- add structured logging export for long-term storage and offline analysis

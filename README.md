# Signal Engine

Production-oriented Solana signal engine for token discovery, deterministic scoring, Discord alerting, live validation, shadow execution, and post-alert learning.

The engine is built to stay explainable. Scoring and gates are explicit, while the learning layer observes outcomes and surfaces where the rules should be reviewed.

## Production Handles

- Live API: `https://signal-engine-e66l.onrender.com`
- Main branch: `main`
- Current production database path: `/var/data/engine.db` through `SIGNAL_ENGINE_DB_PATH`
- Primary operator dashboard: `/learning/ops/readiness/dashboard?hours=6&limit=10`
- Daily opportunities dashboard: `/learning/ops/daily-opportunities/dashboard?hours=6&limit=25`
- Command center: `/learning/command-center/dashboard?hours=24`

## What It Has

### Signal Pipeline

- Helius and Dexscreener-derived token ingestion.
- Deterministic scoring for attention, risk, liquidity, wallet quality, creator quality, promotion, and execution validation.
- Candidate, watch, heating, promoted, breakout, risk, and hard-fail routing.
- Discord alert presentation for live candidates and promoted signals.
- Repeat/collapse handling so repeated alerts are not treated as independent evidence.

### Learning Layer

- SQLite-backed learning tables for signals, decisions, snapshot jobs, snapshots, and reports.
- Outcome windows at immediate, short, medium, and long horizons.
- Labels such as `worked`, `failed`, `faded`, `strong_continuation`, `pending`, and `insufficient_data`.
- Safe production summary endpoints that avoid expensive full-database scans.
- Historical corpus summary for sampled analysis, feature coverage, decision distribution, repeat rate, and blocker pressure.

Key routes:

- `GET /learning/health`
- `GET /learning/history/summary?sample_limit=1000`
- `GET /learning/report/latest`
- `GET /learning/report/{report_date}`
- `GET /learning/digest`
- `GET /learning/digest/dashboard`

### Daily Opportunities

The daily opportunity feed is the main "what should I inspect today?" surface.

It ranks recent live-validation records by:

- positive unsent outcomes
- pending but active watch candidates
- wallet-blocked runners
- route class
- attention score
- risk score
- market-cap movement
- severe safety penalties

It now includes:

- `positive_unsent`: runners that had positive outcomes but were not sent.
- `top_blocker_families`: the gate families currently blocking the most activity.
- `blocker_tuning`: evidence-based recommendations such as `manual_watchlist_override`, `review_relaxation`, `keep_strict`, or `tighten_or_keep_strict`.
- `shadow_summary`: aggregate shadow P&L coverage for the top feed.
- per-opportunity `shadow_execution`: latest shadow position and net P&L when available.

Key routes:

- `GET /learning/ops/daily-opportunities?hours=6&limit=25`
- `GET /learning/ops/daily-opportunities/dashboard?hours=6&limit=25`
- `GET /learning/ops/daily-opportunities/text?hours=6&limit=10`
- `POST /learning/ops/daily-opportunities/send`

The feed is an operator triage surface, not a buy command.

### Readiness Dashboard

The readiness endpoint combines ops health, daily opportunities, blocker tuning, and shadow P&L coverage into one daily operating view.

It returns:

- `ready_state`: `ready` or `needs_review`
- `attention_reasons`
- ops digest severity and counts
- learning health
- positive unsent opportunity count
- shadow P&L coverage
- top opportunities
- blocker tuning recommendations
- operator links for the next dashboards to inspect

Key routes:

- `GET /learning/ops/readiness?hours=6&limit=10`
- `GET /learning/ops/readiness/dashboard?hours=6&limit=10`

Current expected review flags include:

- `positive_unsent_opportunities`
- `blocker_tuning_review_available`
- `shadow_pnl_not_available_for_top_feed`
- ops pressure flags such as `no_sends_with_pressure`

### Shadow Execution

Shadow execution tracks paper positions for validated signals so the engine can compare alerts, misses, and gate decisions against simulated P&L.

It records:

- signal id and token
- entry intent and quote metadata
- transaction intent
- submission lifecycle state
- open/closed status
- entry price
- mark-to-market price and liquidity
- gross and net P&L
- peak/trough P&L
- exit reason
- take-profit, stop-loss, and max-hold settings

Important environment variables:

- `ENABLE_SHADOW_EXECUTION`
- `SHADOW_EXECUTION_POLL_SECONDS`
- `SHADOW_EXECUTION_TAKE_PROFIT_PCT`
- `SHADOW_EXECUTION_STOP_LOSS_PCT`
- `SHADOW_EXECUTION_MAX_HOLD_MINUTES`
- `SHADOW_EXECUTION_ENTRY_FEE_BPS`
- `SHADOW_EXECUTION_EXIT_FEE_BPS`
- `SHADOW_EXECUTION_FIXED_ENTRY_COST_USD`
- `SHADOW_EXECUTION_FIXED_EXIT_COST_USD`

### Ops Digest And Notifications

The ops digest watches engine health, skip pressure, promotion blocks, config drift, rollout notifications, and incident state.

Key routes:

- `GET /learning/ops/digest?hours=24`
- `GET /learning/ops/digest/dashboard?hours=24`
- `GET /learning/ops/digest/text?hours=24`
- `POST /learning/ops/digest/send`
- `GET /learning/tuning/notifications`
- `GET /learning/tuning/notifications/dashboard`
- `GET /learning/tuning/incidents`
- `GET /learning/tuning/incidents/dashboard`
- `POST /learning/tuning/incidents/state`
- `POST /learning/tuning/notifications/{notification_id}/state`

Digest event types:

- `incident_digest`
- `degraded_digest`
- `daily_summary`
- `daily_opportunity_digest`

Webhook delivery:

- `SIGNAL_ENGINE_OPS_WEBHOOK_URL` or `OPS_WEBHOOK_URL` must be configured for outbound ops digest delivery.
- If neither is configured, notifications are recorded but delivery status is `disabled` with `ops_webhook_not_configured`.

### Tuning And Rollouts

The tuning system generates proposals, compares profiles, records approvals, tracks rollout metadata, and verifies behavior after rollout.

Key routes:

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
- `GET /learning/tuning/verification`
- `GET /learning/tuning/verification/dashboard`
- `POST /learning/tuning/verification/apply`
- `POST /learning/tuning/verification/run`

Rollout metadata can be supplied directly or inferred from:

- `SIGNAL_ENGINE_DEPLOY_SERVICE`
- `SIGNAL_ENGINE_DEPLOY_SHA`
- `SIGNAL_ENGINE_DEPLOY_ENV`
- Render metadata such as `RENDER_SERVICE_NAME`, `RENDER_SERVICE_ID`, `RENDER_GIT_COMMIT`, and `RENDER_EXTERNAL_HOSTNAME`

### Command Center

The command center is the broader operator dashboard for regime state, policy actions, incident snapshots, rollout verification, automation runs, and strategy synthesis.

Key routes:

- `GET /learning/command-center?hours=24`
- `GET /learning/command-center/dashboard?hours=24`
- `POST /learning/command-center/regime-action`

### Validation And Missed Runners

Live validation compares sent, skipped, blocked, and pending decisions against later observed outcomes.

Key routes:

- `GET /learning/validation/summary?hours=72&limit=200`
- `GET /learning/validation/dashboard?hours=72&limit=200`
- `GET /learning/validation/alerts?hours=72&limit=100`
- `GET /learning/validation/missed?hours=168&limit=50`
- `GET /learning/validation/policies?hours=168&limit=12`

Use these routes to investigate whether a gate is preventing runners or protecting the system from weak setups.

## System Shape

### Runtime Flow

1. `worker.helius_listener` emits raw token and buy events.
2. `worker.promote.process_event` enriches and scores events.
3. `worker.runner` sends Discord alerts and starts background workers.
4. `app.services.signal_learning_service` records decisions, signals, outcomes, snapshots, and reports.
5. `app.services.shadow_execution_service` records paper execution lifecycle and P&L.
6. `app.services.tuning_service` builds ops digests, readiness summaries, tuning proposals, notifications, and rollout verification.

### Main Directories

- `worker/`
  Runtime ingestion, enrichment, promotion, Discord formatting, execution validation, and background workers.
- `app/services/`
  Learning, tuning, shadow execution, scanning, persistence, and presentation services.
- `app/routes/`
  FastAPI routes for health, scan, score, watch, review, learning, ops, tuning, and dashboards.
- `tests/`
  Regression coverage for scoring, learning, routes, shadow execution, tuning, and ops surfaces.
- `state/`
  Local SQLite state for development. Production uses the configured Render disk path.

## Important Environment Variables

### Database And Runtime Role

- `SIGNAL_ENGINE_DB_PATH`
- `STATE_ENGINE_DB_PATH`
- `SIGNAL_ENGINE_PROCESS_ROLE`
- `SIGNAL_ENGINE_PUBLIC_BASE_URL`
- `SIGNAL_ENGINE_LEARNING_WRITE_BASE_URL`

### Discord And Ops

- `DISCORD_WEBHOOK_URL`
- `DISCORD_CANDIDATE_WEBHOOK`
- `DISCORD_WEBHOOK_NEAR_PASS`
- `DISCORD_WEBHOOK_PASS`
- `DISCORD_WEBHOOK_RUG`
- `DISCORD_WEBHOOK_LOGS`
- `DISCORD_WEBHOOK_DIGEST`
- `SIGNAL_ENGINE_OPS_WEBHOOK_URL`
- `OPS_WEBHOOK_URL`

### Authentication

- `SIGNAL_ENGINE_OPERATOR_API_TOKEN`
- `SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN`
- `SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED`
- `SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC`

Operator and internal-write tokens must be separate random secrets. Operator commands use an `Authorization: Bearer` header; worker writes and guarded storage recovery use `X-Signal-Engine-Token`. Both paths fail closed when their token is not configured.

### Provider Backoff

- `SIGNAL_ENGINE_X_AUTH_COOLDOWN_SEC`
- `SIGNAL_ENGINE_X_RATE_LIMIT_COOLDOWN_SEC`
- `SIGNAL_ENGINE_X_RATE_LIMIT_MAX_COOLDOWN_SEC`
- `SIGNAL_ENGINE_DEX_RATE_LIMIT_COOLDOWN_SEC`
- `SIGNAL_ENGINE_DEX_RATE_LIMIT_MAX_COOLDOWN_SEC`

### Ops Digest Policy

- `SIGNAL_ENGINE_OPS_DIGEST_COOLDOWN_SEC`
- `SIGNAL_ENGINE_OPS_DIGEST_POLL_SEC`
- `SIGNAL_ENGINE_OPS_DIGEST_HOURS`
- `SIGNAL_ENGINE_OPS_DAILY_SUMMARY_HOURS`
- `SIGNAL_ENGINE_OPS_DEGRADED_SKIP_PRESSURE`
- `SIGNAL_ENGINE_OPS_INCIDENT_SKIP_PRESSURE`
- `SIGNAL_ENGINE_OPS_DEGRADED_BLOCK_PRESSURE`
- `SIGNAL_ENGINE_OPS_INCIDENT_BLOCK_PRESSURE`
- `SIGNAL_ENGINE_OPS_INCIDENT_NOTIFICATION_COUNT`
- `SIGNAL_ENGINE_OPS_CRITICAL_DRIFT_PROFILES`
- `SIGNAL_ENGINE_OPS_INCIDENT_ZERO_SEND_MIN_SKIPS`
- `SIGNAL_ENGINE_OPS_DEGRADED_REMINDER_SEC`
- `SIGNAL_ENGINE_OPS_DAILY_SUMMARY_INTERVAL_SEC`

### Rollout Verification

- `SIGNAL_ENGINE_ROLLOUT_VERIFY_POLL_SEC`
- `SIGNAL_ENGINE_ROLLOUT_VERIFY_MIN_AGE_SEC`
- `SIGNAL_ENGINE_REQUIRED_ALIGNED_PROFILES`

## Running Locally

### Requirements

- Python 3.11 preferred
- Environment variables configured locally
- SQLite database path available through `SIGNAL_ENGINE_DB_PATH` or default local state

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

### Run Worker

```bash
python -m worker.runner
```

## Validation

### Full Test Suite

```bash
pytest -q
```

### Focused Ops And Learning Tests

```bash
pytest -q tests/test_learning_route.py tests/test_shadow_execution_service.py
```

### Syntax Check

```bash
python -m compileall app worker tests ocr_token_alert_parser.py
```

## Operating Notes

- Do not claim win rate unless resolved outcome snapshots support it.
- Treat daily opportunities as triage, not financial advice or an automatic buy list.
- Use `blocker_tuning` before loosening guards.
- Keep severe safety gates strict unless there is explicit, reviewed evidence.
- Configure `SIGNAL_ENGINE_OPS_WEBHOOK_URL` if daily digest delivery should leave the API and post to an ops channel.
- The highest-value next upgrade is wallet guard v2 plus shadow P&L coverage for the top opportunity feed.

## Current Next Improvements

- Split wallet guard behavior into hard fraud blocks versus early concentration/manual-watch overrides.
- Increase shadow P&L coverage for positive unsent and wallet-blocked top opportunities.
- Add an explicit "review queue" route for the tokens ranked by `manual_watchlist_override`.
- Add production dashboard links for individual token drilldowns from the daily opportunity table.

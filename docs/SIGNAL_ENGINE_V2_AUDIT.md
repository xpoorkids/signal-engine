# Signal Engine V2 Audit

Audit snapshot captured on 2026-08-26 from starting SHA `fc4f8b0d9fec616ac0789148a2b0cf884030deb3` on branch `signal-engine-v2-worker-v2`.

## Current Event Flow

1. Helius, DEX scanner, and external seed sources create `Event` objects or scanner candidates.
2. `worker.runner` receives queued events, applies in-memory signature dedupe, and calls `worker.promote.process_event()`.
3. `worker.promote` normalizes token state, enriches metadata, runs attention, creator, wallet, DEX, risk, trade validation, candidate EV, and routing gates.
4. `worker.alert_gate` applies candidate and promoted admission checks.
5. `worker.discord` formats and sends `candidate`, `heating_up`, and `promoted` messages while preserving existing event names.
6. `app.services.signal_learning_service` persists decisions, snapshots, outcome observations, tuning evidence, positive-unsent analysis, and reports.
7. `app.services.shadow_execution_service` persists paper-position and shadow execution results.
8. `app.routes.learning`, `app.routes.health`, `app.routes.review`, and `app.routes.scan` expose operations, diagnostics, tuning, review, and command-center surfaces.

## Existing Metric Audit

| Metric | Location | Formula | Unit | Source/window | Missing/freshness behavior | Downstream use | Calibration status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `attention_score` | `worker/attention.py` | Sum of policy-driven boosts from lifecycle, DEX flow, tracked/KOL wallets, X/social, acceleration, and repeat evidence, bounded by policy caps. | 0..1-ish score | Mixed hot-path enrichment; windows vary by source. | Many components use `or 0`; freshness depends on source-specific payload fields. | Candidate/heating/promoted routing, Discord confidence display, EV upside multiplier. | Hand-weighted, not probability calibrated. |
| `risk_score` | `app/services/signal_metrics.py`, `worker/promote.py` | Composite risk from liquidity, holders, contract flags, market shape, wallet/creator evidence, and blockers. | 0..1 risk score | DEX, token metadata, wallet/holder, creator data. | Some unavailable values become blockers; other missing values are normalized through defaults. | Hard fail/risk routes, Discord risk band, trade validation, tuning. | Deterministic rules, not calibrated rug probability. |
| `confidence_score` | `worker/confidence.py`, `worker/promote.py`, `worker/discord.py` | Additive bumps such as `logs_initialize_mint=0.25`, `tx_pump_observed=0.25`, `token_resolved=0.30`, `dex_pair_found=0.35`, `wallet_low_risk=0.20`, `repeat=0.05`, capped by stage. | 0..1 display score | Event progression and enrichment state. | Missing evidence means no bump; not tied to source agreement or historical reliability. | Discord display, route scoring, tuning. | Displayed as confidence but not calibrated evidence confidence. |
| `creator_score` | `worker/creator_score.py` | Starts at policy base, subtracts high 24h deploys, low lifetime deploys, cluster funding; adds prior profitable, old wallet, and low-frequency bonuses; clamps 0..1. | 0..1 score | Creator stats from state. | Unknown creator returns `0.0` with `creator_unknown`. | Promotion and routing support. | Heuristic, not outcome-calibrated. |
| `elite_score` | `worker/elite.py` | Point system over early quality signals, wallet quality, DEX shape, and risk filters. | Integer score | Mixed event/enrichment. | Missing fields generally contribute no points. | Sniper/heating routing, Discord quality. | Heuristic point score. |
| `dex_flow_quality_score` | `app/services/score_service.py` | Liquidity +18/+10, volume +14, buys +18/+10, buy/sell +22, volume/liquidity +14, sane price change +14. Tier: confirmed >=75, developing >=55, else weak. | Points | DexScreener 5m pair snapshot. | Missing DEX values become zero and failures such as thin liquidity/low volume. | Candidate confirmation, blockers, diagnostics, Discord reasons. | Heuristic DEX-shape score. |
| `wallet_risk` | `worker/wallet_risk.py`, `app/services/wallet_service.py` | Rugcheck risk maxed into score; odd `1111` prefix flag; app service adds holder concentration logic. | 0..1 risk | Rugcheck and holder data. | `requests` exceptions are swallowed; unavailable Rugcheck leaves score `0.0`. | Risk, hard fail, routing. | Not calibrated; missing external source can look low risk. |
| `liquidity_factor` | `worker/dex.py`, `worker/trade_validator.py`, policy helpers | Threshold-based liquidity floors and reserve-derived slippage/impact. | USD or normalized factor | DexScreener pair/liquidity snapshot. | Missing liquidity often becomes zero or blocker depending caller. | Candidate gates, trade validation, EV, Discord. | Heuristic threshold. |
| `execution_edge` | `worker/execution.py` | Stub returns `edge_bps=0.0`, `reasons=[]`, `size_cap_usd=0.0`. | bps/USD | None. | Always present as zero. | Future hook only. | Unimplemented. |
| `candidate_ev` | `worker/expected_value.py` | `gross_upside_bps = base_upside_bps * (0.70 + attention * 0.60)`, `risk_penalty_bps = risk * risk_penalty_bps`, `net_edge_bps = gross_upside_bps - round_trip_slippage_bps - risk_penalty_bps`. | bps | Trade validation and DEX summary. | Missing trade validation rejects; slippage missing defaults to `0.0`. | Candidate EV gate. | Heuristic net edge, not predicted EV. |
| `progression` | `worker/progression.py` | Improvement if attention delta, unique buyer delta, liquidity pct improvement, or score delta exceeds policy thresholds. | Boolean/reasons | Prior/current in-memory state. | Missing values become zero. | Repeat/heating progression. | Heuristic. |
| Route confirmation counts | `worker/signal_policy.py`, `worker/alert_gate.py` | Counts named confirmation signals such as breadth, DEX, social, wallet, and market support. | Count/list | Mixed. | Missing confirmation omitted. | Candidate/heating/promoted gates. | Rule-based. |
| Market regime | `worker/signal_policy.py`, tuning services | Policy profile and outcome summaries are present; no versioned persisted `RegimeSnapshot` contract yet. | Label/threshold set | Learning history and policy config. | Not consistently exposed as fresh metric. | Tuning and policy guidance. | Heuristic profile, not regime model. |
| Blocker tuning | `app/services/tuning_service.py`, `app/services/parameter_search_service.py` | Outcome summaries by blocker and parameter grids propose tighten/hold/review actions. | Counts/rates | SQLite learning outcomes. | Depends on available labels and stored decisions. | Operator tuning approvals. | Evidence-informed but not ML-calibrated. |

## Major Problems Located

- Missing data is sometimes converted to zero (`worker/progression.py`, `app/services/score_service.py`, `worker/wallet_risk.py`).
- Staleness is not represented by a typed metric state across all important metrics.
- `attention_score`, `confidence_score`, `creator_score`, `elite_score`, route confidence, and DEX flow quality are fixed-weight or point systems.
- Several underlying facts feed multiple scores, especially DEX flow, buyer breadth, liquidity, wallet concentration, and risk blockers.
- Blocking `requests` calls remain in runtime paths: `app/services/dex_service.py`, `app/services/discord_service.py`, `app/services/j7tracker_service.py`, `worker/wallet_risk.py`, and `worker/route_quote.py`.
- Source adapters lack a shared async client with bounded concurrency, retry budgets, and circuit-breaker state.
- Runner dedupe and cooldowns are in memory and restart-sensitive.
- `worker.runner` can hold the process open after fatal failure, and `asyncio.gather(..., return_exceptions=True)` can keep the process alive after task failure.
- Discord delivery state is still mixed with signal persistence in runner flow; a failed Discord send prevents some signal persistence.
- Existing outcome analysis includes last/reference price paths; executable quote quality is not consistently separated.
- Jupiter adapter currently uses the older quote path and is not versioned as Swap V2.
- Holder concentration needs explicit exclusion of pools, burns, programs, bonding curves, and known custody before V2 should treat it as effective circulating supply.

## Baseline Report

Machine-readable baseline: `artifacts/signal_engine_v2_baseline.json`.

Live baseline over the 3 hour lookback:

- Deployed SHA: `fc4f8b0d9fec616ac0789148a2b0cf884030deb3`
- Service status: `degraded`, dependency `x_signal:http_401`
- Decisions sampled by health endpoint: `5000`
- Sent: `14`
- Skipped: `3166`
- Blocked: `0`
- Diagnostic sample: `999` skips, `1` emit
- Hard-fail diagnostic count: `284`
- Candidate-gate skip diagnostic count: `684`
- Storage: writable, `/var/data/engine.db`, `49459200` bytes
- DEX scanner: `266` pairs in latest producer metadata
- Tests after initial V2 contracts: `339 passed`, with `6` FastAPI deprecation warnings
- Not currently exposed: queue depth, latency p50/p95, enrichment latency by source, duplicate rate, stale metric rate, worker CPU, and worker memory

No improvement is claimed from this baseline. It records current behavior before V2 behavior changes.

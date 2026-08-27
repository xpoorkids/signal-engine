# Manual Action Engine

The action engine is manual decision support for the operator. It never executes
trades, constructs transactions, requests wallet signing authority, asks for a
private key, asks for a seed phrase, or changes `EXECUTION_MODE`.

Feature flags:

- `SIGNAL_ENGINE_ACTION_ENGINE_ENABLED=0`
- `SIGNAL_ENGINE_ACTION_ENGINE_SHADOW=1`
- `SIGNAL_ENGINE_DEFAULT_RISK_PROFILE=aggressive`
- `SIGNAL_ENGINE_DEFAULT_EXIT_STYLE=catalyst_runner`

Default profile:

- risk profile: `AGGRESSIVE`
- exit style: `CATALYST_RUNNER`
- execution mode: `MANUAL`
- calibration: `HEURISTIC_UNCALIBRATED`

## Actions

Pre-entry actions:

- `BUY NOW`
- `BUY SMALL`
- `CATALYST BUY NOW`
- `CATALYST BUY SMALL`
- `WAIT`
- `WAIT FOR PULLBACK`
- `DO NOT CHASE`
- `AVOID`
- `HARD FAIL`

Owned-position actions:

- `HOLD`
- `ADD SMALL ON CONFIRMATION`
- `TAKE PROFIT`
- `RECOVER PRINCIPAL`
- `TRIM`
- `HOLD RUNNER`
- `HOLD MOON BAG`
- `CATALYST WEAKENING`
- `CATALYST INVALIDATED`
- `SELL NOW`
- `EMERGENCY EXIT`

Buy and profit actions display a `SHADOW` suffix while the engine is uncalibrated.
Deterministic safety exits may display without `SHADOW`.

## Recommendation Inputs

Opportunity remains separate from safety, execution, and confidence. The service
does not collapse these into a single mystery score.

Opportunity:

- probability target is reached before invalidation
- estimated net return
- momentum continuation
- catalyst upside

Safety:

- rug-like event probability
- liquidity failure probability
- sell-route failure probability
- contract safety
- creator and insider risk
- wallet concentration and manipulation risk

Execution:

- intended position size
- buy impact
- sell impact
- round-trip cost
- route availability
- maximum safe size
- quote freshness

Confidence:

- data completeness
- source freshness
- source agreement
- historical calibration status
- catalyst verification quality

## Starting Entry Policy

Normal target concept: `+25% before -18% within 15 minutes`.

`BUY NOW SHADOW` requires no hard fail, valid buy and sell routes, fresh quote,
data confidence at least 70%, target-before-invalidation at least 55%, estimated
net edge at least +6%, failure risk no higher than 12%, round-trip cost no higher
than 5%, two organic flow windows, independent wallet or fee-commitment
confirmation, stable liquidity, acceptable holder distribution, and no creator or
insider selling.

`BUY SMALL SHADOW` is for positive but incomplete setups. It requires no hard
fail, valid sell route, data confidence at least 60%, target-before-invalidation
at least 48%, estimated net edge at least +2%, and failure risk no higher than
15%. Starting size is about 25% to 40% of normal intended size.

`WAIT` is used when the setup is positive but missing confirmation, liquidity is
not yet stable, data is incomplete, or the intended-size exit quote is stale.

`WAIT FOR PULLBACK` or `DO NOT CHASE` is used when price is extended from the
preferred entry and expected return no longer supports the current execution
costs. Extension is evaluated with flow, liquidity, holder growth, and expected
return, not by one fixed chart rule alone.

## Persistence

Every recommendation is persisted in `action_recommendations` with policy,
feature, model, calibration, payload, and optional outcome data. This is shadow
learning evidence, not proof of profitability.
# Operator X Identity Guard

The action engine now consults the operator-managed X identity blocklist before
allowing positive pre-entry or add-on recommendations.

- Stable numeric X ID matches on authoritative token/developer/creator links
  return `HARD FAIL` with blocker `operator_blocked_x_identity`.
- Verified rename-history matches return `HARD FAIL` with blockers
  `operator_blocked_x_identity` and `blocked_x_rename_lineage`.
- Exact current-handle or historical-alias matches without stable-ID proof return
  `AVOID` with blockers `blocked_x_handle_match_unresolved` and
  `stable_x_identity_unresolved`.
- Reposts and incidental mentions create review exposure, not automatic hard
  failure.

This guard does not alter buy thresholds, catalyst policy, Discord routing, or
execution mode. It only prevents positive manual buy/add recommendations where
the operator has explicitly blocked a public project identity lineage.


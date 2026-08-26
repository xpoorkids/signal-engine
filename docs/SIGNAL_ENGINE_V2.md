# Signal Engine V2

Signal Engine V2 is an additive shadow intelligence layer. The current deterministic rules engine remains the production champion until an explicit operator approval changes model mode.

## Principles

- Hard safety gates remain separate from opportunity scoring.
- Missing is not zero, stale is not fresh, and unavailable is not bearish.
- Opportunity, safety, execution, confidence, and regime stay separate.
- Probabilities are only displayed after calibration evidence exists.
- Live trading is not enabled by this work.

## Current Implementation

- `app/models/metrics.py` defines typed `MetricValue` records with status, unit, provenance, timestamps, age, window, completeness, confidence, reasons, feature version, and calibration status.
- `app/models/assessment.py` defines `OpportunityAssessment`, assessment layers, lifecycle labels, regime labels, and V2 action labels mapped to legacy event types.
- `worker/features/formulas.py` defines initial reusable formulas for bounded order-flow imbalance, safe ratios, HHI, entropy, and Gini.
- `worker/features/fee_commitment.py` defines the shadow Fee Commitment and Fee Authenticity feature family. It separates network fees, priority fees, protocol trading fees, creator fees generated, and creator fees claimed. It also tracks fee payer, trade authority, token buyer, funding cluster, and sponsor/router identity separately.

## Fee Commitment

Fee activity is one evidence family, not a standalone buy signal and not a hard minimum. It may support a setup only when independent and organic, and it becomes a warning when concentrated, creator-funded, bot/sybil-linked, dust-driven, failed-transaction-heavy, or disconnected from genuine buy notional.

The initial implementation emits shadow classifications only:

- `ORGANIC_FEE_COMMITMENT`
- `FEE_ACTIVITY_BUILDING`
- `FEE_ACTIVITY_CONFIRMED`
- `LOW_FEE_EVIDENCE`
- `FEE_PAYER_CONCENTRATION`
- `CREATOR_FUNDED_ACTIVITY`
- `BOT_FEE_SPAM`
- `FAILED_TRANSACTION_SPAM`
- `DUST_FEE_MANIPULATION`
- `PROTOCOL_FEE_WASH_TRADING`

Before routing use, thresholds must be normalized and backtested by token age, lifecycle stage, venue, liquidity, market cap, and market regime.

## Compatibility

V2 assessments should be added under `signal_engine_v2` in existing event payloads. Legacy event types remain:

- `WATCH` maps to `candidate`
- `HEATING` maps to `heating_up`
- `VALIDATED` maps to `promoted`

## Runtime Flags

- `SIGNAL_ENGINE_V2_ENABLED`
- `SIGNAL_ENGINE_V2_SHADOW`
- `SIGNAL_ENGINE_V2_COMPARE`
- `SIGNAL_ENGINE_V2_MODEL_MODE`
- `SIGNAL_ENGINE_V2_HELIUS_TRANSACTION_SUBSCRIBE`
- `SIGNAL_ENGINE_V2_JUPITER_SWAP_V2`
- `SIGNAL_ENGINE_V2_BIRDEYE_STREAM`
- `SIGNAL_ENGINE_V2_POSTGRES`
- `SIGNAL_ENGINE_V2_VALKEY`

Default mode is rules champion with V2 in shadow only.

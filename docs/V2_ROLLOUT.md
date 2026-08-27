# V2 Rollout

## Order

1. Security repair
2. Audit and baseline
3. Metric contract and freshness
4. Worker supervision and async source clients
5. Durable idempotency, cooldowns, checkpoints, and delivery outbox
6. Jupiter V2 quote adapter
7. Token-2022 safety
8. Flow and execution features
9. Holder and wallet features
10. Immutable outcome snapshots
11. V1/V2 shadow comparison
12. Calibrated rule outputs
13. ML challenger in shadow
14. Explicit approval
15. Controlled production adoption

## Guardrails

- Do not loosen hard safety gates automatically.
- Do not change production thresholds merely because V2 features exist.
- Do not enable live trading.
- Require resolved, out-of-sample, size-aware shadow outcomes before claiming profitability or approving model-led decisions.
# Manual Action Engine Rollout

The manual action engine is disabled by default and shadow-only when enabled.
Rollout requires:

1. keep `DRY_RUN=1` and `EXECUTION_MODE=shadow`
2. enable `SIGNAL_ENGINE_ACTION_ENGINE_ENABLED=1`
3. keep `SIGNAL_ENGINE_ACTION_ENGINE_SHADOW=1`
4. verify recommendations persist in `action_recommendations`
5. verify no wallet secret, private key, seed phrase, transaction construction,
   signing, or submission path exists
6. compare shadow outcomes before changing any production behavior

This rollout does not deploy or merge by itself.
# Offline Research Corpus

`signal-engine-v2-research-corpus` is a child branch for offline historical research. It must not deploy, merge to `main`, alter live thresholds, or enable live execution. Outputs are used to audit and validate current V2 metrics and action recommendations before any later policy proposal.
# Operator X Identity Blocklist

The operator X blocklist is safe to roll out as manual decision support. It does
not execute trades, alter execution mode, loosen gates, or change scoring
thresholds.

Before using it operationally:

- seed `config/operator_x_identity_blocklist.yaml` through
  `POST /x-identities/seed`;
- add stable numeric X IDs when reliable evidence is available;
- link token socials only when the association type is authoritative;
- treat unresolved exact alias matches as manual-review `AVOID`;
- keep repost-only and mention-only exposure out of deterministic hard-fail
  routing;
- keep `SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED=0` unless operator mutation
  routes must be used;
- set `SIGNAL_ENGINE_OPERATOR_API_TOKEN` only in secret stores when management is
  enabled;
- keep `SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC=0` unless exposing blocklist reads
  is intentional.

Seed application is non-destructive and tracked in
`x_identity_seed_migrations`. A disabled seeded block remains disabled across
service restarts and same-version seed checks. Force restore requires an
authenticated explicit seed request.

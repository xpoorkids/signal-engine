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

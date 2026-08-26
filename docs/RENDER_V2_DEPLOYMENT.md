# Render V2 Deployment

Render deployment should keep the current services compatible while V2 runs in shadow comparison.

## Required Environment Defaults

- `EXECUTION_MODE=shadow`
- `DRY_RUN=1` for any execution subsystem
- `SIGNAL_ENGINE_V2_ENABLED=false` until the branch is merged and validated
- `SIGNAL_ENGINE_V2_SHADOW=true`
- `SIGNAL_ENGINE_V2_MODEL_MODE=rules`

## Operational Checks

Before claiming healthy V2 behavior, verify:

- live health
- worker heartbeat and producer metadata
- durable storage write probe
- current-policy emits
- Discord delivery result
- diagnostics and resolved outcomes

Heartbeat alone is not sufficient.

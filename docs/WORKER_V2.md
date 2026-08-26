# Worker V2

Worker V2 should be introduced incrementally behind feature flags. The current worker remains compatible until the new durable stages pass replay and live shadow comparison.

## Target Stages

`INGEST -> NORMALIZE -> PERSIST RAW EVENT -> UPDATE ROLLING STATE -> CRITICAL ENRICHMENT -> BASE FEATURE BUILD -> SAFETY -> FAST ASSESSMENT -> OPTIONAL ENRICHMENT -> ENRICHED ASSESSMENT -> ROUTING -> DELIVERY OUTBOX -> LEARNING SNAPSHOT -> OUTCOME OBSERVATION`

## Findings From Current Worker

- In-memory dedupe uses signature/type/token and does not survive restarts.
- Cooldowns are in memory and do not survive restarts.
- Discord delivery and decision persistence remain coupled in runner flow.
- Critical task death can leave the process alive.
- Blocking HTTP calls remain in runtime paths.

## Required Next Slice

Add durable raw events, processing state, decision records, delivery outbox, checkpoints, and dead-letter rows behind SQLite-compatible repository interfaces. Then move runner persistence order to:

1. persist assessment and decision
2. create delivery outbox record
3. attempt Discord delivery
4. record delivery result
5. update sent-message state after confirmed success

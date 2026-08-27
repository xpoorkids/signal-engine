# Worker V2

Worker V2 is an incremental reliability path behind `SIGNAL_ENGINE_WORKER_V2_ENABLED`.
The default is `0`, so production keeps legacy runtime behavior unless the flag is
explicitly enabled. This slice does not change trading metrics, thresholds, fee
authenticity formulas, manual contract-address assessment logic, execution mode,
or Discord formatting.

## Implemented Runtime Stages

The implemented vertical slice is:

1. receive queue event
2. build deterministic durable event identity
3. persist raw event in `worker_events`
4. atomically claim processing with a lease
5. skip completed duplicates and live leased events
6. run existing `process_event`
7. persist Worker dispatch decisions
8. reserve cooldowns before delivery when delivery is eligible
9. create a Discord delivery outbox row before transport
10. mark outbox attempting
11. call the existing Discord formatter/sender
12. persist delivery result
13. commit or release cooldown
14. run legacy post-delivery persistence only after confirmed success
15. mark raw event completed
16. advance checkpoints

Legacy mode retains the existing in-memory behavior, except the queue accounting
bug fix that guarantees exactly one `q.task_done()` per successful `q.get()`.

## Feature Flags

`SIGNAL_ENGINE_WORKER_V2_ENABLED=0` keeps Worker V2 disabled by default.

`SIGNAL_ENGINE_WORKER_V2_MAX_EVENT_ATTEMPTS=3` controls the durable maximum event
attempts before an event is retained as a dead letter.

`SIGNAL_ENGINE_WORKER_V2_EVENT_LEASE_SECONDS=120` controls processing lease TTL.
Live leases prevent simultaneous processing; expired leases can be reclaimed.

## Event Identity

Durable event IDs use SHA-256 over a documented canonical identity string. The
identity version is `worker-event-id-v1`. The generated `Event.id` is never part
of durable identity because it changes on replay or reconstruction.

Signature-based identity:

`worker-event-id-v1|{source}|{signature}|{event_type}|{token}|ix={instruction_index}`

The instruction index suffix is included when available in `extra` as
`instruction_index`, `instruction_idx`, `ix_index`, or `inner_instruction_index`.
When no instruction index exists, the signature/type/token behavior is preserved.

Unsigned events use this priority:

1. explicit source event ID from `extra.source_event_id`, `source_id`, `event_id`,
   `request_id`, or `assessment_id`
2. DEX scanner identity `dex_scan:{token}:{scan_window_or_scan_started_ts}`
3. recheck identity `recheck:{token}:{stage}:{scheduled_timestamp}`
4. slot, token, and event type when slot exists
5. minute timestamp bucket plus canonical stable payload fields

Volatile processing timestamps, generated UUIDs, retry metadata, and source-health
snapshots are excluded from the generic stable payload.

## Runtime Tables

`worker_events` stores raw events and processing state. Status values are
`received`, `processing`, `completed`, `failed`, and `dead_letter`.

`worker_dispatch_decisions` records decisions before Discord is contacted.
Dispositions include derived, eligible, suppressed, pending, sent, failed, and
uncertain outcomes.

`worker_delivery_outbox` records intended Discord delivery before network I/O.
It stores safe destination labels such as `main` and `candidate`; it never stores
webhook URLs, authorization headers, API keys, or secrets.

`worker_cooldowns` stores confirmed delivery timestamps and active reservations.

`worker_checkpoints` stores source and worker progress with monotonic slot updates
when a Solana slot exists.

`worker_dead_letters` stores retained failures that reached max attempts or could
not complete safely.

## Lease Behavior

Event claims use `BEGIN IMMEDIATE` and short transactions. A new raw event is
inserted if absent, then moved to `processing` with a lease owner and expiration.
A completed duplicate is skipped. A live lease owned by another worker is skipped.
An expired lease can be reclaimed, incrementing the attempt count. When the max
attempt count is reached, the event is marked `dead_letter` and retained without
automatic replay.

## Cooldown Reservations

Worker V2 does not use the existing persistent `allow_alert()` path as the
authoritative cooldown because that path advances send state before transport is
confirmed.

Cooldown flow:

1. reserve `promoted:{token}`, `sniper:{token}`, `heating_up:{token}`, or
   `candidate:{token}` atomically
2. create outbox
3. attempt Discord
4. commit `last_delivered_ts` only after confirmed success
5. release the reservation after definitive failure
6. keep the reservation for ambiguous attempted delivery

This intentionally biases Discord delivery toward at-most-once behavior after
network ambiguity. The slice does not implement automatic resend for uncertain
delivery.

## Decision And Outbox Sequence

The Worker never contacts Discord before both a dispatch decision and an outbox
row exist. Suppressed outcomes are persisted as decisions. Dry run, missing
webhook, disabled delivery, candidate_send suppression, cooldown suppression, and
heating quality suppression are distinguishable.

Legacy `record_signal_event()` remains a delivered-signal history API. Worker V2
calls it only after confirmed Discord success so existing dashboards remain
compatible.

## Delivery Results

Discord delivery now has a richer `DeliveryResult` model with:

`success`, `attempted`, `message_id`, `status_code`, `reason`, `error_type`,
`error_message`, `retryable`, and `ambiguous`.

`send_discord(e)` remains a boolean compatibility wrapper. Worker V2 uses
`send_discord_result(e)` and the existing candidate sender returns the richer
result shape.

## Checkpoints

Worker V2 advances `source:{source}:completed` and
`worker:event_loop:completed` after a raw event completes. Checkpoints do not
claim replay/resume support yet. They provide restart visibility, gap
investigation, monotonic progress tracking, and groundwork for a later replay
slice. Older slots cannot move a checkpoint backward; slotless events can still
update timestamp metadata.

## Dead Letters

Repeated processing failures create or update `worker_dead_letters`, retaining
sanitized payload and error details. Repository functions can list recent dead
letters, retrieve one, mark it manually reviewed, and reset one to replayable.
No public replay write endpoint and no automatic replay loop are included.

## Task Supervision

When Worker V2 is enabled, storage write failure at startup is fatal. Critical
tasks are the event loop, heartbeat, and Helius listener when enabled. If a
critical task raises, returns unexpectedly, or is cancelled, Worker V2 marks
health unhealthy, cancels remaining tasks, propagates failure, and exits non-zero
so Render can restart the process.

Optional tasks use bounded restart with exponential backoff, jitter, restart
counters, and last-error logging. Optional task exceptions are not silently
ignored.

Legacy mode keeps the previous gather/hold-open behavior.

## Health Metadata

Existing heartbeat metadata includes a `worker_v2` object. When enabled it
contains:

- enabled flag and worker instance ID
- pending, processing, failed, and dead-letter event counts
- oldest pending age
- active event leases
- pending, attempting, failed, and uncertain outbox counts
- most recent successful and failed delivery timestamps
- cooldown count
- latest checkpoints
- critical task health
- optional task restart counts

The query uses indexed aggregate lookups and bounded checkpoint reads.

## Current Limitations

This slice does not implement replay/resume, automatic outbox retry, PostgreSQL,
Redis, Kafka, Temporal, Kubernetes, live trading, source circuit breakers,
non-blocking shared HTTP clients, Helius enhanced WebSocket comparison, or
machine learning.

The next Worker V2 slice should focus on non-blocking shared HTTP clients,
source-specific timeouts and retry budgets, circuit breakers, Helius enhanced
WebSocket shadow comparison, and optional source degradation handling.

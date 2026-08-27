# Historical Action Replay

Action replay feeds point-in-time snapshots into `app.services.action_engine_service.ActionEngineService`.

The replay does not rewrite the action engine and does not change production behavior.

## Replay Rules

- Snapshots are processed chronologically.
- Only features observed at or before the snapshot timestamp are passed in.
- Simulated positions are research-only.
- Profiles compared: `BALANCED`, `AGGRESSIVE`, `AGGRESSIVE_CATALYST_RUNNER`.
- Intended sizes compared: `$100`, `$250`, `$500`.

Replay outputs include entries, partial sells, principal recovery, runner retention, realized P&L, unrealized P&L, drawdown, and safety exits when the point-in-time action engine recommends them.


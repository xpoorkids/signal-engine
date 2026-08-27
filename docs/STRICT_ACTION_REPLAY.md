# Strict Action Replay

Source-mode action replay reuses `app.services.action_engine_service.ActionEngineService` but passes data through a strict historical adapter.

In strict mode:

- missing price remains missing
- missing liquidity remains missing
- missing sell route remains unknown
- missing execution cost remains unavailable
- missing wallet confirmation remains unavailable

When required evidence is missing, the replay row records `replay_status=insufficient_evidence` and does not generate a synthetic `BUY NOW`.

Production action-engine thresholds and defaults are unchanged.

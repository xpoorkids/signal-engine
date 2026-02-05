previous_stage != current_stage
{
    "event": "stage_transition",
    "token": token_id,
    "chain": "sol",
    "from_stage": previous_stage,
    "to_stage": current_stage,
    "score": decision.score,
    "reasons": decision.reasons,
    "entered_at": prev_stage_entered_at,
    "exited_at": now_iso,
    "duration_seconds": seconds_in_prev_stage,
}

def update_watch_state(token_key, chain, signals):
    previous = load_watch_state(token_key)

    decision = classify_watch_stage(signals)

    persist_stage_state(
        token_key=token_key,
        chain=chain,
        previous_state=previous,
        decision=decision,
    )

    save_watch_state(
        token_key=token_key,
        stage=decision.stage,
        score=decision.score,
        entered_at=now,
    )

    return decision

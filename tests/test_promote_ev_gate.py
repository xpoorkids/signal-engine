from worker.promote import _apply_candidate_ev_gate


def test_candidate_ev_gate_blocks_send_when_trade_validation_missing():
    extra = {}

    send_eligible, send_reasons, candidate_ev = _apply_candidate_ev_gate(
        send_eligible=True,
        send_reasons=[],
        extra=extra,
        dex_summary={"liquidity_usd": 75000.0},
        attention_score=0.82,
        risk_score=0.12,
    )

    assert send_eligible is False
    assert "ev_gate:trade_validation_missing" in send_reasons
    assert candidate_ev["approved"] is False
    assert candidate_ev["reasons"] == ["trade_validation_missing"]
    assert extra["candidate_ev"] == candidate_ev


def test_candidate_ev_gate_records_missing_validation_without_rewriting_existing_skip():
    extra = {}

    send_eligible, send_reasons, candidate_ev = _apply_candidate_ev_gate(
        send_eligible=False,
        send_reasons=["quality_confirmation_missing"],
        extra=extra,
        dex_summary=None,
        attention_score=0.22,
        risk_score=0.38,
    )

    assert send_eligible is False
    assert send_reasons == ["quality_confirmation_missing"]
    assert candidate_ev["approved"] is False
    assert candidate_ev["reasons"] == ["trade_validation_missing"]
    assert extra["candidate_ev"] == candidate_ev

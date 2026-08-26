from datetime import datetime, timedelta, timezone

import pytest

from app.models.assessment import DecisionAction, OpportunityAssessment
from app.models.metrics import MetricStatus, MetricValue
from worker.features.formulas import (
    bounded_order_flow_imbalance,
    gini_coefficient,
    herfindahl_hirschman_index,
    safe_ratio,
    wallet_entropy,
)


def test_missing_metric_does_not_accept_zero_fallback_value():
    metric = MetricValue.missing("liquidity_usd", unit="usd", reasons=("source_unavailable",))

    assert metric.value is None
    assert metric.status == MetricStatus.MISSING
    assert metric.completeness == 0.0
    assert metric.confidence == 0.0

    with pytest.raises(ValueError):
        MetricValue("liquidity_usd", 0, "usd", MetricStatus.MISSING)


def test_metric_age_and_stale_status_are_explicit():
    observed = datetime.now(timezone.utc) - timedelta(seconds=10)
    metric = MetricValue.computed(
        "buy_count_5s",
        4,
        unit="count",
        observed_at=observed,
        window_seconds=5,
        source_names=("helius",),
    )

    stale = metric.mark_stale_if_older_than(5_000)

    assert stale.status == MetricStatus.STALE
    assert stale.age_ms is not None and stale.age_ms >= 10_000
    assert stale.confidence <= 0.2


def test_flow_imbalance_is_bounded_and_missing_safe():
    assert bounded_order_flow_imbalance(None, 10) is None
    assert bounded_order_flow_imbalance(10, 0) == 1.0
    assert bounded_order_flow_imbalance(0, 10) == -1.0
    assert bounded_order_flow_imbalance(5, 5) == 0.0
    with pytest.raises(ValueError):
        bounded_order_flow_imbalance(-1, 1)


def test_concentration_formulas_handle_boundaries():
    assert safe_ratio(1, 0) is None
    assert herfindahl_hirschman_index([]) is None
    assert herfindahl_hirschman_index([50, 50]) == pytest.approx(0.5)
    assert wallet_entropy([50, 50]) == pytest.approx(0.693147, rel=1e-5)
    assert gini_coefficient([1, 1, 1]) == pytest.approx(0.0)
    assert gini_coefficient([0, 0, 10]) == pytest.approx(2 / 3)


def test_opportunity_assessment_preserves_legacy_event_mapping():
    assessment = OpportunityAssessment(token="mint", action=DecisionAction.WATCH)
    payload = assessment.to_legacy_payload_extension()

    assert assessment.legacy_event_type == "candidate"
    assert payload["signal_engine_v2"]["legacy_event_type"] == "candidate"
    assert payload["signal_engine_v2"]["policy_version"] == "rules_champion_v1"
    assert payload["signal_engine_v2"]["model_version"] == "none"

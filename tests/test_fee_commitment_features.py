import pytest

from app.models.metrics import MetricStatus
from worker.features.fee_commitment import (
    FeeActivityClass,
    FeeObservation,
    compute_fee_window_features,
)


def _metric(features, name):
    return next(metric for metric in features.metrics if metric.name == name)


def test_fee_features_separate_fee_types_and_identities():
    features = compute_fee_window_features(
        [
            FeeObservation(
                signature="a",
                observed_ts=1,
                network_fee_sol=0.001,
                priority_fee_sol=0.002,
                protocol_trading_fee_sol=0.003,
                creator_fee_generated_sol=0.004,
                creator_fee_claimed_sol=0.005,
                side="buy",
                trade_notional_sol=1.5,
                fee_payer="payer_a",
                trade_authority="authority_a",
                token_buyer="buyer_a",
                funding_cluster="cluster_a",
            ),
            FeeObservation(
                signature="b",
                observed_ts=2,
                network_fee_sol=0.001,
                priority_fee_sol=0.001,
                side="sell",
                trade_notional_sol=0.5,
                fee_payer="payer_b",
                trade_authority="authority_b",
                token_buyer="buyer_b",
                funding_cluster="cluster_b",
            ),
        ],
        window_seconds=60,
    )

    assert _metric(features, "network_fee_sol").value == pytest.approx(0.002)
    assert _metric(features, "priority_fee_sol").value == pytest.approx(0.003)
    assert _metric(features, "protocol_trading_fee_sol").value == pytest.approx(0.003)
    assert _metric(features, "creator_fee_generated_sol").value == pytest.approx(0.004)
    assert _metric(features, "creator_fee_claimed_sol").value == pytest.approx(0.005)
    assert _metric(features, "unique_fee_payers").value == 2
    assert _metric(features, "unique_trade_authorities").value == 2


def test_total_fee_alone_is_low_evidence_not_bullish():
    features = compute_fee_window_features(
        [
            FeeObservation(
                signature="a",
                observed_ts=1,
                network_fee_sol=0.5,
                fee_payer="one_payer",
                funding_cluster="one_cluster",
            )
        ],
        window_seconds=60,
    )

    assert FeeActivityClass.ORGANIC_FEE_COMMITMENT not in features.classifications
    assert FeeActivityClass.FEE_PAYER_CONCENTRATION in features.classifications
    assert _metric(features, "total_fee_sol").value == pytest.approx(0.5)


def test_organic_fee_requires_independent_clusters():
    features = compute_fee_window_features(
        [
            FeeObservation(signature="a", observed_ts=1, network_fee_sol=0.01, side="buy", trade_notional_sol=1, fee_payer="p1", funding_cluster="c1"),
            FeeObservation(signature="b", observed_ts=2, network_fee_sol=0.01, side="buy", trade_notional_sol=1, fee_payer="p2", funding_cluster="c2"),
            FeeObservation(signature="c", observed_ts=3, network_fee_sol=0.01, side="buy", trade_notional_sol=1, fee_payer="p3", funding_cluster="c3"),
        ],
        window_seconds=60,
    )

    assert FeeActivityClass.ORGANIC_FEE_COMMITMENT in features.classifications
    assert _metric(features, "organic_fee_ratio").value == pytest.approx(1.0)
    assert _metric(features, "fee_concentration_hhi").value == pytest.approx(1 / 3)


def test_fee_warning_classes_capture_manipulation_shapes():
    features = compute_fee_window_features(
        [
            FeeObservation(
                signature="a",
                observed_ts=1,
                network_fee_sol=0.04,
                protocol_trading_fee_sol=0.06,
                success=False,
                fee_payer="p1",
                funding_cluster="c1",
                creator_connected=True,
                bot_or_sybil_cluster=True,
                dust_trade=True,
                suspected_protocol_wash=True,
            ),
        ],
        window_seconds=60,
    )

    assert FeeActivityClass.FAILED_TRANSACTION_SPAM in features.classifications
    assert FeeActivityClass.CREATOR_FUNDED_ACTIVITY in features.classifications
    assert FeeActivityClass.BOT_FEE_SPAM in features.classifications
    assert FeeActivityClass.DUST_FEE_MANIPULATION in features.classifications
    assert FeeActivityClass.PROTOCOL_FEE_WASH_TRADING in features.classifications


def test_fee_acceleration_is_missing_without_prior_window():
    features = compute_fee_window_features(
        [FeeObservation(signature="a", observed_ts=1, network_fee_sol=0.01, fee_payer="p1")],
        window_seconds=60,
    )

    acceleration = _metric(features, "fee_acceleration_sol_per_second")
    assert acceleration.status == MetricStatus.MISSING
    assert acceleration.value is None


def test_negative_fee_amount_is_rejected():
    with pytest.raises(ValueError):
        compute_fee_window_features(
            [FeeObservation(signature="a", observed_ts=1, network_fee_sol=-0.01)],
            window_seconds=60,
        )

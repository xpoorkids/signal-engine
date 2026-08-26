from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.models.metrics import MetricValue
from worker.features.formulas import herfindahl_hirschman_index, safe_ratio


FEATURE_VERSION = "signal_engine_v2_fee_commitment@1"


class FeeActivityClass(str, Enum):
    ORGANIC_FEE_COMMITMENT = "ORGANIC_FEE_COMMITMENT"
    FEE_ACTIVITY_BUILDING = "FEE_ACTIVITY_BUILDING"
    FEE_ACTIVITY_CONFIRMED = "FEE_ACTIVITY_CONFIRMED"
    LOW_FEE_EVIDENCE = "LOW_FEE_EVIDENCE"
    FEE_PAYER_CONCENTRATION = "FEE_PAYER_CONCENTRATION"
    CREATOR_FUNDED_ACTIVITY = "CREATOR_FUNDED_ACTIVITY"
    BOT_FEE_SPAM = "BOT_FEE_SPAM"
    FAILED_TRANSACTION_SPAM = "FAILED_TRANSACTION_SPAM"
    DUST_FEE_MANIPULATION = "DUST_FEE_MANIPULATION"
    PROTOCOL_FEE_WASH_TRADING = "PROTOCOL_FEE_WASH_TRADING"


@dataclass(frozen=True)
class FeeObservation:
    signature: str
    observed_ts: float
    network_fee_sol: float = 0.0
    priority_fee_sol: float = 0.0
    protocol_trading_fee_sol: float = 0.0
    creator_fee_generated_sol: float = 0.0
    creator_fee_claimed_sol: float = 0.0
    success: bool = True
    side: str | None = None
    trade_notional_sol: float | None = None
    trade_notional_usd: float | None = None
    fee_payer: str | None = None
    trade_authority: str | None = None
    token_buyer: str | None = None
    funding_cluster: str | None = None
    sponsor_or_router: str | None = None
    creator_connected: bool = False
    bot_or_sybil_cluster: bool = False
    dust_trade: bool = False
    suspected_protocol_wash: bool = False

    @property
    def total_fee_sol(self) -> float:
        return (
            self.network_fee_sol
            + self.priority_fee_sol
            + self.protocol_trading_fee_sol
            + self.creator_fee_generated_sol
            + self.creator_fee_claimed_sol
        )

    def validate(self) -> None:
        amounts = (
            self.network_fee_sol,
            self.priority_fee_sol,
            self.protocol_trading_fee_sol,
            self.creator_fee_generated_sol,
            self.creator_fee_claimed_sol,
        )
        if any(not math.isfinite(float(value)) or float(value) < 0 for value in amounts):
            raise ValueError("fee amounts must be finite and non-negative")
        if self.trade_notional_sol is not None and (not math.isfinite(float(self.trade_notional_sol)) or self.trade_notional_sol < 0):
            raise ValueError("trade_notional_sol must be finite and non-negative")
        if self.trade_notional_usd is not None and (not math.isfinite(float(self.trade_notional_usd)) or self.trade_notional_usd < 0):
            raise ValueError("trade_notional_usd must be finite and non-negative")


@dataclass(frozen=True)
class FeeWindowFeatures:
    window_seconds: int
    metrics: tuple[MetricValue, ...]
    classifications: tuple[FeeActivityClass, ...]
    positive_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    feature_version: str = FEATURE_VERSION
    calibration_status: str = "shadow_unvalidated"
    threshold_notes: tuple[str, ...] = (
        "shadow_only",
        "no_standalone_bullish_signal",
        "normalize_thresholds_by_age_lifecycle_venue_liquidity_market_cap_and_regime_before_routing_use",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_seconds": self.window_seconds,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "classifications": [item.value for item in self.classifications],
            "positive_reasons": list(self.positive_reasons),
            "warnings": list(self.warnings),
            "feature_version": self.feature_version,
            "calibration_status": self.calibration_status,
            "threshold_notes": list(self.threshold_notes),
        }


def _sum(values: list[float]) -> float:
    return round(sum(values), 12)


def _unique(values: list[str | None]) -> set[str]:
    return {str(value).strip() for value in values if str(value or "").strip()}


def _share(part: float, total: float) -> float | None:
    return safe_ratio(part, total)


def _cluster_key(obs: FeeObservation) -> str | None:
    return obs.funding_cluster or obs.sponsor_or_router or obs.fee_payer


def _metric(name: str, value: float | int | str | bool | None, unit: str, window_seconds: int, reasons: tuple[str, ...] = ()) -> MetricValue:
    if value is None:
        return MetricValue.missing(
            name,
            unit=unit,
            reasons=reasons or ("insufficient_fee_observations",),
            feature_version=FEATURE_VERSION,
        )
    return MetricValue.computed(
        name,
        value,
        unit=unit,
        window_seconds=window_seconds,
        source_names=("solana_transaction_fees",),
        reasons=reasons,
        feature_version=FEATURE_VERSION,
        calibration_status="shadow_unvalidated",
    )


def compute_fee_window_features(
    observations: list[FeeObservation],
    *,
    window_seconds: int,
    previous: FeeWindowFeatures | None = None,
) -> FeeWindowFeatures:
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    for observation in observations:
        observation.validate()

    total_fee = _sum([obs.total_fee_sol for obs in observations])
    successful_fee = _sum([obs.total_fee_sol for obs in observations if obs.success])
    failed_fee = _sum([obs.total_fee_sol for obs in observations if not obs.success])
    buy_fee = _sum([obs.total_fee_sol for obs in observations if (obs.side or "").lower() == "buy"])
    sell_fee = _sum([obs.total_fee_sol for obs in observations if (obs.side or "").lower() == "sell"])
    organic_fee = _sum(
        [
            obs.total_fee_sol
            for obs in observations
            if obs.success
            and not obs.creator_connected
            and not obs.bot_or_sybil_cluster
            and not obs.dust_trade
            and not obs.suspected_protocol_wash
        ]
    )
    genuine_buy_notional = _sum(
        [
            float(obs.trade_notional_sol or 0.0)
            for obs in observations
            if (obs.side or "").lower() == "buy" and obs.success and not obs.dust_trade
        ]
    )
    fee_payers = _unique([obs.fee_payer for obs in observations])
    trade_authorities = _unique([obs.trade_authority for obs in observations])

    payer_totals: dict[str, float] = defaultdict(float)
    cluster_totals: dict[str, float] = defaultdict(float)
    for obs in observations:
        if obs.fee_payer:
            payer_totals[obs.fee_payer] += obs.total_fee_sol
        cluster = _cluster_key(obs)
        if cluster:
            cluster_totals[cluster] += obs.total_fee_sol

    top_payer_share = max((_share(value, total_fee) or 0.0 for value in payer_totals.values()), default=None) if total_fee > 0 else None
    top_cluster_share = max((_share(value, total_fee) or 0.0 for value in cluster_totals.values()), default=None) if total_fee > 0 else None
    fee_hhi = herfindahl_hirschman_index(payer_totals.values())
    creator_fee_share = _share(_sum([obs.total_fee_sol for obs in observations if obs.creator_connected]), total_fee)
    bot_fee_share = _share(_sum([obs.total_fee_sol for obs in observations if obs.bot_or_sybil_cluster]), total_fee)
    dust_fee_share = _share(_sum([obs.total_fee_sol for obs in observations if obs.dust_trade]), total_fee)
    failed_fee_share = _share(failed_fee, total_fee)
    protocol_wash_share = _share(_sum([obs.protocol_trading_fee_sol for obs in observations if obs.suspected_protocol_wash]), total_fee)
    fee_per_independent_wallet = _share(total_fee, len(cluster_totals))
    fee_relative_to_buy_notional = _share(total_fee, genuine_buy_notional)
    fee_velocity = total_fee / window_seconds

    previous_velocity = None
    if previous:
        prior = {metric.name: metric.value for metric in previous.metrics}
        try:
            previous_velocity = float(prior.get("fee_velocity_sol_per_second"))
        except Exception:
            previous_velocity = None
    fee_acceleration = None if previous_velocity is None else fee_velocity - previous_velocity

    classifications: list[FeeActivityClass] = []
    positive_reasons: list[str] = []
    warnings: list[str] = []

    if not observations or total_fee <= 0:
        classifications.append(FeeActivityClass.LOW_FEE_EVIDENCE)
        warnings.append("fee_evidence_missing_or_zero")
    if len(cluster_totals) >= 3 and (top_cluster_share is None or top_cluster_share <= 0.50) and organic_fee > 0:
        classifications.append(FeeActivityClass.ORGANIC_FEE_COMMITMENT)
        positive_reasons.append("fee_activity_spread_across_independent_clusters")
    if fee_velocity > 0 and fee_acceleration is not None and fee_acceleration > 0:
        classifications.append(FeeActivityClass.FEE_ACTIVITY_BUILDING)
        positive_reasons.append("fee_velocity_accelerating")
    if len(cluster_totals) >= 3 and organic_fee > 0 and failed_fee_share is not None and failed_fee_share <= 0.20:
        classifications.append(FeeActivityClass.FEE_ACTIVITY_CONFIRMED)
    if top_payer_share is not None and top_payer_share >= 0.60:
        classifications.append(FeeActivityClass.FEE_PAYER_CONCENTRATION)
        warnings.append("fee_spend_concentrated_in_single_payer")
    if top_cluster_share is not None and top_cluster_share >= 0.60:
        classifications.append(FeeActivityClass.FEE_PAYER_CONCENTRATION)
        warnings.append("fee_spend_concentrated_in_single_cluster")
    if creator_fee_share is not None and creator_fee_share >= 0.40:
        classifications.append(FeeActivityClass.CREATOR_FUNDED_ACTIVITY)
        warnings.append("creator_connected_fee_share_elevated")
    if bot_fee_share is not None and bot_fee_share >= 0.40:
        classifications.append(FeeActivityClass.BOT_FEE_SPAM)
        warnings.append("bot_or_sybil_fee_share_elevated")
    if failed_fee_share is not None and failed_fee_share >= 0.35:
        classifications.append(FeeActivityClass.FAILED_TRANSACTION_SPAM)
        warnings.append("failed_transaction_fee_share_elevated")
    if dust_fee_share is not None and dust_fee_share >= 0.35:
        classifications.append(FeeActivityClass.DUST_FEE_MANIPULATION)
        warnings.append("dust_trade_fee_share_elevated")
    if protocol_wash_share is not None and protocol_wash_share >= 0.30:
        classifications.append(FeeActivityClass.PROTOCOL_FEE_WASH_TRADING)
        warnings.append("protocol_fee_wash_share_elevated")

    metrics = (
        _metric("total_fee_sol", total_fee, "sol", window_seconds),
        _metric("successful_transaction_fee_sol", successful_fee, "sol", window_seconds),
        _metric("failed_transaction_fee_sol", failed_fee, "sol", window_seconds),
        _metric("buy_associated_fee_sol", buy_fee, "sol", window_seconds),
        _metric("sell_associated_fee_sol", sell_fee, "sol", window_seconds),
        _metric("unique_fee_payers", len(fee_payers), "count", window_seconds),
        _metric("unique_trade_authorities", len(trade_authorities), "count", window_seconds),
        _metric("independent_fee_payer_clusters", len(cluster_totals), "count", window_seconds),
        _metric("top_fee_payer_share", top_payer_share, "ratio", window_seconds),
        _metric("top_fee_cluster_share", top_cluster_share, "ratio", window_seconds),
        _metric("fee_concentration_hhi", fee_hhi, "ratio", window_seconds),
        _metric("creator_connected_fee_share", creator_fee_share, "ratio", window_seconds),
        _metric("bot_sybil_cluster_fee_share", bot_fee_share, "ratio", window_seconds),
        _metric("dust_trade_fee_share", dust_fee_share, "ratio", window_seconds),
        _metric("fee_per_independent_wallet_sol", fee_per_independent_wallet, "sol", window_seconds),
        _metric("fee_relative_to_genuine_buy_notional", fee_relative_to_buy_notional, "ratio", window_seconds),
        _metric("fee_velocity_sol_per_second", fee_velocity, "sol_per_second", window_seconds),
        _metric("fee_acceleration_sol_per_second", fee_acceleration, "sol_per_second_delta", window_seconds),
        _metric("fee_persistence", bool(total_fee > 0 and previous is not None), "boolean", window_seconds),
        _metric("organic_fee_sol", organic_fee, "sol", window_seconds),
        _metric("organic_fee_ratio", _share(organic_fee, total_fee), "ratio", window_seconds),
        _metric("network_fee_sol", _sum([obs.network_fee_sol for obs in observations]), "sol", window_seconds),
        _metric("priority_fee_sol", _sum([obs.priority_fee_sol for obs in observations]), "sol", window_seconds),
        _metric("protocol_trading_fee_sol", _sum([obs.protocol_trading_fee_sol for obs in observations]), "sol", window_seconds),
        _metric("creator_fee_generated_sol", _sum([obs.creator_fee_generated_sol for obs in observations]), "sol", window_seconds),
        _metric("creator_fee_claimed_sol", _sum([obs.creator_fee_claimed_sol for obs in observations]), "sol", window_seconds),
    )

    deduped_classes = tuple(dict.fromkeys(classifications))
    return FeeWindowFeatures(
        window_seconds=window_seconds,
        metrics=metrics,
        classifications=deduped_classes,
        positive_reasons=tuple(dict.fromkeys(positive_reasons)),
        warnings=tuple(dict.fromkeys(warnings)),
    )

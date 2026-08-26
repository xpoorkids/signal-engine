from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from app.models.metrics import MetricValue, utc_now


ASSESSMENT_VERSION = "signal_engine_v2_assessment@1"
POLICY_VERSION = "rules_champion_v1"
MODEL_VERSION = "none"


class LifecycleStage(str, Enum):
    JUST_CREATED = "just_created"
    ACTIVE_BONDING_CURVE = "active_bonding_curve"
    APPROACHING_MIGRATION = "approaching_migration"
    RECENTLY_MIGRATED = "recently_migrated"
    ESTABLISHED_DEX_POOL = "established_dex_pool"
    DORMANT_TOKEN = "dormant_token"
    REVIVED_TOKEN = "revived_token"
    COMMUNITY_TAKEOVER = "community_takeover"
    UNKNOWN = "unknown_lifecycle"


class RegimeLabel(str, Enum):
    HOT_EXPANSION = "HOT_EXPANSION"
    HEALTHY = "HEALTHY"
    SELECTIVE = "SELECTIVE"
    COLD = "COLD"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    CHAOTIC = "CHAOTIC"
    RUG_HEAVY = "RUG_HEAVY"
    NETWORK_STRESSED = "NETWORK_STRESSED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class DecisionAction(str, Enum):
    IGNORE = "IGNORE"
    OBSERVE = "OBSERVE"
    WATCH = "WATCH"
    HEATING = "HEATING"
    VALIDATED = "VALIDATED"
    SHADOW_ENTRY_ELIGIBLE = "SHADOW_ENTRY_ELIGIBLE"
    AVOID = "AVOID"
    HARD_FAIL = "HARD_FAIL"


LEGACY_EVENT_TYPE_BY_ACTION = {
    DecisionAction.WATCH: "candidate",
    DecisionAction.HEATING: "heating_up",
    DecisionAction.VALIDATED: "promoted",
}


@dataclass(frozen=True)
class AssessmentLayer:
    name: str
    metrics: tuple[MetricValue, ...] = ()
    status: str = "insufficient_data"
    positive_reasons: tuple[str, ...] = ()
    negative_reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "positive_reasons": list(self.positive_reasons),
            "negative_reasons": list(self.negative_reasons),
            "blockers": list(self.blockers),
            "confidence": max(0.0, min(1.0, float(self.confidence))),
        }


@dataclass(frozen=True)
class OpportunityAssessment:
    token: str
    action: DecisionAction
    decision_ts: datetime = field(default_factory=utc_now)
    assessment_id: str = field(default_factory=lambda: str(uuid4()))
    lifecycle_stage: LifecycleStage = LifecycleStage.UNKNOWN
    market_regime: RegimeLabel = RegimeLabel.INSUFFICIENT_DATA
    layers: tuple[AssessmentLayer, ...] = ()
    probabilities: tuple[MetricValue, ...] = ()
    expected_values: tuple[MetricValue, ...] = ()
    execution_metrics: tuple[MetricValue, ...] = ()
    intended_size_usd: float | None = None
    maximum_safe_shadow_size_usd: float | None = None
    data_confidence: MetricValue | None = None
    model_confidence: MetricValue | None = None
    source_completeness: MetricValue | None = None
    source_freshness: MetricValue | None = None
    tags: tuple[str, ...] = ()
    positive_reasons: tuple[str, ...] = ()
    negative_reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    feature_version: str = ASSESSMENT_VERSION
    policy_version: str = POLICY_VERSION
    model_version: str = MODEL_VERSION
    source_snapshot_ids: tuple[str, ...] = ()

    @property
    def legacy_event_type(self) -> str | None:
        return LEGACY_EVENT_TYPE_BY_ACTION.get(self.action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "token": self.token,
            "decision_ts": self.decision_ts.isoformat(),
            "lifecycle_stage": self.lifecycle_stage.value,
            "market_regime": self.market_regime.value,
            "layers": [layer.to_dict() for layer in self.layers],
            "probabilities": [metric.to_dict() for metric in self.probabilities],
            "expected_values": [metric.to_dict() for metric in self.expected_values],
            "execution_metrics": [metric.to_dict() for metric in self.execution_metrics],
            "intended_size_usd": self.intended_size_usd,
            "maximum_safe_shadow_size_usd": self.maximum_safe_shadow_size_usd,
            "data_confidence": self.data_confidence.to_dict() if self.data_confidence else None,
            "model_confidence": self.model_confidence.to_dict() if self.model_confidence else None,
            "source_completeness": self.source_completeness.to_dict() if self.source_completeness else None,
            "source_freshness": self.source_freshness.to_dict() if self.source_freshness else None,
            "action": self.action.value,
            "legacy_event_type": self.legacy_event_type,
            "tags": list(self.tags),
            "positive_reasons": list(self.positive_reasons),
            "negative_reasons": list(self.negative_reasons),
            "blockers": list(self.blockers),
            "feature_version": self.feature_version,
            "policy_version": self.policy_version,
            "model_version": self.model_version,
            "source_snapshot_ids": list(self.source_snapshot_ids),
        }

    def to_legacy_payload_extension(self) -> dict[str, Any]:
        return {"signal_engine_v2": self.to_dict()}

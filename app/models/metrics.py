from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


FEATURE_VERSION = "signal_engine_v2_metrics@1"


class MetricStatus(str, Enum):
    COMPUTED = "computed"
    MISSING = "missing"
    STALE = "stale"
    INSUFFICIENT_DATA = "insufficient_data"
    SOURCE_UNAVAILABLE = "source_unavailable"
    NOT_APPLICABLE = "not_applicable"
    DISABLED = "disabled"
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _bounded(value: float | None, *, default: float) -> float:
    if value is None:
        return default
    if not math.isfinite(float(value)):
        raise ValueError("metric confidence and completeness must be finite")
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class MetricValue:
    name: str
    value: float | int | str | bool | None
    unit: str
    status: MetricStatus
    source_names: tuple[str, ...] = ()
    observed_at: datetime | None = None
    computed_at: datetime = field(default_factory=utc_now)
    age_ms: int | None = None
    window_seconds: int | None = None
    completeness: float = 1.0
    confidence: float = 1.0
    reasons: tuple[str, ...] = ()
    feature_version: str = FEATURE_VERSION
    calibration_status: str = "uncalibrated"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("metric name is required")
        if self.status in {MetricStatus.MISSING, MetricStatus.SOURCE_UNAVAILABLE} and self.value is not None:
            raise ValueError("missing or unavailable metrics must not carry numeric fallback values")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("metric value must be finite")
        object.__setattr__(self, "completeness", _bounded(self.completeness, default=1.0))
        object.__setattr__(self, "confidence", _bounded(self.confidence, default=1.0))
        if self.age_ms is None and self.observed_at is not None:
            observed = self.observed_at
            computed = self.computed_at
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            if computed.tzinfo is None:
                computed = computed.replace(tzinfo=timezone.utc)
            age = max(0, int((computed - observed).total_seconds() * 1000))
            object.__setattr__(self, "age_ms", age)

    @classmethod
    def computed(
        cls,
        name: str,
        value: float | int | str | bool,
        *,
        unit: str,
        source_names: tuple[str, ...] = (),
        observed_at: datetime | None = None,
        window_seconds: int | None = None,
        confidence: float = 1.0,
        completeness: float = 1.0,
        reasons: tuple[str, ...] = (),
        feature_version: str = FEATURE_VERSION,
        calibration_status: str = "uncalibrated",
    ) -> "MetricValue":
        return cls(
            name=name,
            value=value,
            unit=unit,
            status=MetricStatus.COMPUTED,
            source_names=source_names,
            observed_at=observed_at,
            window_seconds=window_seconds,
            confidence=confidence,
            completeness=completeness,
            reasons=reasons,
            feature_version=feature_version,
            calibration_status=calibration_status,
        )

    @classmethod
    def missing(
        cls,
        name: str,
        *,
        unit: str,
        status: MetricStatus = MetricStatus.MISSING,
        source_names: tuple[str, ...] = (),
        reasons: tuple[str, ...] = (),
        feature_version: str = FEATURE_VERSION,
    ) -> "MetricValue":
        return cls(
            name=name,
            value=None,
            unit=unit,
            status=status,
            source_names=source_names,
            completeness=0.0,
            confidence=0.0,
            reasons=reasons,
            feature_version=feature_version,
        )

    def as_stale(self, reason: str = "source_age_exceeded") -> "MetricValue":
        return MetricValue(
            name=self.name,
            value=self.value,
            unit=self.unit,
            status=MetricStatus.STALE,
            source_names=self.source_names,
            observed_at=self.observed_at,
            computed_at=self.computed_at,
            age_ms=self.age_ms,
            window_seconds=self.window_seconds,
            completeness=self.completeness,
            confidence=min(self.confidence, 0.2),
            reasons=(*self.reasons, reason),
            feature_version=self.feature_version,
            calibration_status=self.calibration_status,
        )

    def mark_stale_if_older_than(self, max_age_ms: int) -> "MetricValue":
        if self.age_ms is not None and self.age_ms > max_age_ms:
            return self.as_stale()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "status": self.status.value,
            "source_names": list(self.source_names),
            "observed_at": _iso(self.observed_at),
            "computed_at": _iso(self.computed_at),
            "age_ms": self.age_ms,
            "window_seconds": self.window_seconds,
            "completeness": self.completeness,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "feature_version": self.feature_version,
            "calibration_status": self.calibration_status,
        }

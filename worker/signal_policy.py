from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return int(default)


@dataclass(frozen=True)
class MarketQualityThresholds:
    min_liq: float
    min_vol5m: float
    min_buys5m: float
    max_sell_ratio5m: float
    max_vol_liq_ratio5m: float
    max_price_drop5m: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateSignalPolicy:
    min_unique_buyers_5m: int
    min_burst_count_60s: int
    min_confirmation_signals: int
    min_send_confirmation_signals: int
    min_market_support_liq_usd: float
    creator_attention_floor: float
    creator_attention_target: float
    strong_attention_threshold: float
    exceptional_attention_threshold: float
    strong_creator_threshold: float
    anti_wash_top_wallet_share: float
    anti_wash_unique_wallets_30s: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromotionSignalPolicy:
    base_confirmations: int
    low_quality_extra_confirmations: int
    strong_signal_confirmation_discount: int
    strong_liquidity_multiplier: float
    strong_buyer_buffer: int
    strong_attention_buffer: float
    strong_risk_buffer: float
    max_sell_ratio5m: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RouteSignalPolicy:
    heating_min_attention: float
    heating_min_liq_usd: float
    heating_min_x_mentions: int
    heating_min_x_authors: int
    heating_age_bypass_ttl_sec: int
    sniper_min_attention: float
    sniper_min_unique_10s: int
    sniper_min_burst_10s: int
    sniper_min_elite: int
    sniper_min_confirmations: int
    sniper_fast_track_attention: float
    sniper_fast_track_confirmations: int
    sniper_age_bypass_ttl_sec: int
    heating_min_confirmations: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_signal_policy() -> CandidateSignalPolicy:
    return CandidateSignalPolicy(
        min_unique_buyers_5m=_env_int("SIGNAL_ENGINE_CANDIDATE_MIN_UNIQUE_BUYERS_5M", 3),
        min_burst_count_60s=_env_int("SIGNAL_ENGINE_CANDIDATE_MIN_BURST_60S", 6),
        min_confirmation_signals=_env_int("SIGNAL_ENGINE_CANDIDATE_MIN_CONFIRMATIONS", 2),
        min_send_confirmation_signals=_env_int("SIGNAL_ENGINE_CANDIDATE_MIN_SEND_CONFIRMATIONS", 3),
        min_market_support_liq_usd=_env_float("SIGNAL_ENGINE_CANDIDATE_MIN_MARKET_SUPPORT_LIQ_USD", 12000.0),
        creator_attention_floor=_env_float("SIGNAL_ENGINE_CANDIDATE_CREATOR_ATTENTION_FLOOR", 0.35),
        creator_attention_target=_env_float("SIGNAL_ENGINE_CANDIDATE_CREATOR_ATTENTION_TARGET", 0.42),
        strong_attention_threshold=_env_float("SIGNAL_ENGINE_CANDIDATE_STRONG_ATTENTION_THRESHOLD", 0.58),
        exceptional_attention_threshold=_env_float("SIGNAL_ENGINE_CANDIDATE_EXCEPTIONAL_ATTENTION_THRESHOLD", 0.72),
        strong_creator_threshold=_env_float("SIGNAL_ENGINE_CANDIDATE_STRONG_CREATOR_THRESHOLD", 0.65),
        anti_wash_top_wallet_share=_env_float("SIGNAL_ENGINE_ANTI_WASH_TOP_WALLET_SHARE", 0.70),
        anti_wash_unique_wallets_30s=_env_int("SIGNAL_ENGINE_ANTI_WASH_MAX_UNIQUE_WALLETS_30S", 2),
    )


def promotion_signal_policy() -> PromotionSignalPolicy:
    return PromotionSignalPolicy(
        base_confirmations=_env_int("SIGNAL_ENGINE_PROMOTED_BASE_CONFIRMATIONS", 2),
        low_quality_extra_confirmations=_env_int("SIGNAL_ENGINE_PROMOTED_LOW_QUALITY_EXTRA_CONFIRMATIONS", 1),
        strong_signal_confirmation_discount=_env_int("SIGNAL_ENGINE_PROMOTED_STRONG_SIGNAL_CONFIRMATION_DISCOUNT", 1),
        strong_liquidity_multiplier=_env_float("SIGNAL_ENGINE_PROMOTED_STRONG_LIQUIDITY_MULTIPLIER", 1.75),
        strong_buyer_buffer=_env_int("SIGNAL_ENGINE_PROMOTED_STRONG_BUYER_BUFFER", 8),
        strong_attention_buffer=_env_float("SIGNAL_ENGINE_PROMOTED_STRONG_ATTENTION_BUFFER", 0.08),
        strong_risk_buffer=_env_float("SIGNAL_ENGINE_PROMOTED_STRONG_RISK_BUFFER", 0.12),
        max_sell_ratio5m=_env_float("SIGNAL_ENGINE_PROMOTED_MAX_SELL_RATIO_5M", 1.15),
    )


def route_signal_policy() -> RouteSignalPolicy:
    return RouteSignalPolicy(
        heating_min_attention=_env_float("SIGNAL_ENGINE_HEATING_MIN_ATTENTION", 0.45),
        heating_min_liq_usd=_env_float("SIGNAL_ENGINE_HEATING_MIN_LIQ_USD", 15000.0),
        heating_min_x_mentions=_env_int("SIGNAL_ENGINE_HEATING_MIN_X_MENTIONS", 10),
        heating_min_x_authors=_env_int("SIGNAL_ENGINE_HEATING_MIN_X_AUTHORS", 10),
        heating_age_bypass_ttl_sec=_env_int("SIGNAL_ENGINE_HEATING_AGE_BYPASS_TTL_SECONDS", 12),
        sniper_min_attention=_env_float("SIGNAL_ENGINE_SNIPER_MIN_ATTENTION", 0.55),
        sniper_min_unique_10s=_env_int("SIGNAL_ENGINE_SNIPER_MIN_UNIQUE_10S", 2),
        sniper_min_burst_10s=_env_int("SIGNAL_ENGINE_SNIPER_MIN_BURST_10S", 6),
        sniper_min_elite=_env_int("SIGNAL_ENGINE_SNIPER_MIN_ELITE", 8),
        sniper_min_confirmations=_env_int("SIGNAL_ENGINE_SNIPER_MIN_CONFIRMATIONS", 2),
        sniper_fast_track_attention=_env_float("SIGNAL_ENGINE_SNIPER_FAST_TRACK_ATTENTION", 0.65),
        sniper_fast_track_confirmations=_env_int("SIGNAL_ENGINE_SNIPER_FAST_TRACK_CONFIRMATIONS", 3),
        sniper_age_bypass_ttl_sec=_env_int("SIGNAL_ENGINE_SNIPER_AGE_BYPASS_TTL_SECONDS", 20),
        heating_min_confirmations=_env_int("SIGNAL_ENGINE_HEATING_MIN_CONFIRMATIONS", 2),
    )


def market_quality_thresholds_for_age(age_min: float) -> MarketQualityThresholds:
    if age_min < 2.0:
        return MarketQualityThresholds(20000.0, 12000.0, 20.0, 1.3, 4.0, -8.0)
    if age_min < 10.0:
        return MarketQualityThresholds(12000.0, 7000.0, 12.0, 1.6, 6.0, -12.0)
    return MarketQualityThresholds(8000.0, 5000.0, 8.0, 2.0, 8.0, -18.0)


def candidate_confirmation_signals(
    *,
    attention_score: float,
    extra: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    policy = candidate_signal_policy()
    payload = extra if isinstance(extra, dict) else {}
    metrics = payload.get("attention_metrics") if isinstance(payload.get("attention_metrics"), dict) else {}
    if not metrics and not dex_summary:
        return [], []
    reasons: list[str] = []
    confirmations: list[str] = []

    buyers_5m = int(metrics.get("unique_buyers_5m") or 0)
    burst_60s = int(metrics.get("burst_count_60s") or 0)
    tracked_hits = int(metrics.get("tracked_wallet_hits") or 0)
    kol_hits = int(metrics.get("kol_wallet_hits") or 0)
    x_mentions = int(metrics.get("x_tweet_count") or 0)
    x_authors = int(metrics.get("x_unique_authors") or 0)
    narrative_hits = metrics.get("narrative_hits") if isinstance(metrics.get("narrative_hits"), list) else []
    top_wallet_share = float(metrics.get("top_wallet_share_30s") or 0.0)
    unique_wallets_30s = int(metrics.get("unique_wallets_30s") or 0)

    if buyers_5m >= policy.min_unique_buyers_5m:
        confirmations.append("buyer_breadth")
    if burst_60s >= policy.min_burst_count_60s:
        confirmations.append("burst_strength")
    if tracked_hits > 0:
        confirmations.append("tracked_wallet_flow")
    if kol_hits > 0:
        confirmations.append("kol_wallet_flow")
    if attention_score >= policy.strong_attention_threshold:
        confirmations.append("strong_attention")
    if x_mentions >= 5 and x_authors >= 3:
        confirmations.append("social_support")
    if narrative_hits:
        confirmations.append("narrative_alignment")

    liq = 0.0
    buys5m = 0
    if isinstance(dex_summary, dict):
        try:
            liq = float(dex_summary.get("liquidity_usd") or 0.0)
        except Exception:
            liq = 0.0
        try:
            buys5m = int(dex_summary.get("txns_m5_buys") or 0)
        except Exception:
            buys5m = 0
    if liq >= policy.min_market_support_liq_usd and buys5m >= 8:
        confirmations.append("market_support")

    if (
        top_wallet_share >= policy.anti_wash_top_wallet_share
        and unique_wallets_30s <= policy.anti_wash_unique_wallets_30s
        and tracked_hits == 0
        and kol_hits == 0
    ):
        reasons.append("concentrated_wallet_flow")

    if len(confirmations) < policy.min_confirmation_signals:
        reasons.append(
            f"confirmation_signals<{policy.min_confirmation_signals}"
        )
    return reasons, confirmations


def candidate_send_reasons(
    *,
    attention_score: float | None,
    creator_score: float,
    extra: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> tuple[bool, list[str], list[str]]:
    policy = candidate_signal_policy()
    attn = float(attention_score or 0.0)
    reasons, confirmations = candidate_confirmation_signals(
        attention_score=attn,
        extra=extra,
        dex_summary=dex_summary,
    )
    confirmation_set = set(confirmations)
    quality_confirmed = bool(
        {"tracked_wallet_flow", "kol_wallet_flow", "market_support", "social_support", "narrative_alignment"} & confirmation_set
    )

    has_creator_support = creator_score >= policy.strong_creator_threshold and attn >= policy.creator_attention_floor
    has_attention_only = attn >= policy.strong_attention_threshold
    has_balanced_quality = creator_score >= policy.creator_attention_target and attn >= policy.creator_attention_target

    if len(confirmations) < policy.min_send_confirmation_signals and attn < policy.exceptional_attention_threshold:
        reasons.append(f"send_confirmation_signals<{policy.min_send_confirmation_signals}")
    if not quality_confirmed and attn < policy.exceptional_attention_threshold:
        reasons.append("quality_confirmation_missing")

    eligible = (has_attention_only or has_creator_support or has_balanced_quality) and not reasons
    if not (has_attention_only or has_creator_support or has_balanced_quality):
        reasons.append("attention_creator_alignment_missing")
    return eligible, reasons, confirmations


def promotion_confirmation_target(
    *,
    confidence_score: float,
    confidence_min: float,
    attention_score: float | None,
    attention_min: float,
    risk_score: float | None,
    risk_max: float,
    liquidity_usd: float,
    liquidity_min: float,
    buyers_15m: int,
    buyers_15m_min: int,
    extra: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> tuple[int, list[str]]:
    policy = promotion_signal_policy()
    metrics = extra.get("attention_metrics") if isinstance(extra, dict) and isinstance(extra.get("attention_metrics"), dict) else {}
    tracked_hits = int(metrics.get("tracked_wallet_hits") or 0)
    kol_hits = int(metrics.get("kol_wallet_hits") or 0)
    attention = float(attention_score or 0.0)
    risk = float(risk_score) if isinstance(risk_score, (int, float)) else None
    reasons: list[str] = []

    strong_signals = 0
    if confidence_score >= (confidence_min + 0.05):
        strong_signals += 1
        reasons.append("confidence_buffer")
    if attention >= (attention_min + policy.strong_attention_buffer):
        strong_signals += 1
        reasons.append("attention_buffer")
    if liquidity_usd >= (liquidity_min * policy.strong_liquidity_multiplier):
        strong_signals += 1
        reasons.append("liquidity_buffer")
    if buyers_15m >= (buyers_15m_min + policy.strong_buyer_buffer):
        strong_signals += 1
        reasons.append("buyer_buffer")
    if risk is not None and risk <= max(0.0, risk_max - policy.strong_risk_buffer):
        strong_signals += 1
        reasons.append("risk_buffer")
    if tracked_hits > 0 or kol_hits > 0:
        strong_signals += 1
        reasons.append("smart_money_support")

    confirm_target = policy.base_confirmations
    if strong_signals >= 4:
        confirm_target = max(1, confirm_target - policy.strong_signal_confirmation_discount)
    elif strong_signals <= 1:
        confirm_target = confirm_target + policy.low_quality_extra_confirmations

    if isinstance(dex_summary, dict):
        buys5m = int(dex_summary.get("txns_m5_buys") or 0)
        sells5m = int(dex_summary.get("txns_m5_sells") or 0)
        sell_ratio = (float(sells5m) / float(buys5m)) if buys5m > 0 else 0.0
        if sell_ratio > policy.max_sell_ratio5m:
            confirm_target += 1
            reasons.append("sell_pressure_high")
    return confirm_target, reasons


def classify_route_signal(
    *,
    attention_score: float | None,
    elite_score: int,
    unique_10s: int,
    burst_10s: int,
    hard_fail_from_authority_checks: bool,
    extra: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    policy = route_signal_policy()
    payload = extra if isinstance(extra, dict) else {}
    metrics = payload.get("attention_metrics") if isinstance(payload.get("attention_metrics"), dict) else {}
    liq = float(dex_summary.get("liquidity_usd") or 0.0) if isinstance(dex_summary, dict) else 0.0
    buys5m = int(dex_summary.get("txns_m5_buys") or 0) if isinstance(dex_summary, dict) else 0
    tracked_hits = int(metrics.get("tracked_wallet_hits") or 0)
    kol_hits = int(metrics.get("kol_wallet_hits") or 0)
    buyers_5m = int(metrics.get("unique_buyers_5m") or 0)
    burst_60s = int(metrics.get("burst_count_60s") or 0)
    x_mentions = int(metrics.get("x_tweet_count") or 0)
    x_authors = int(metrics.get("x_unique_authors") or 0)
    attention = float(attention_score or 0.0)

    confirmations: list[str] = []
    blockers: list[str] = []
    sniper_blockers: list[str] = []
    route_tier = "watch"

    if tracked_hits > 0:
        confirmations.append("tracked_wallet_flow")
    if kol_hits > 0:
        confirmations.append("kol_wallet_flow")
    if buyers_5m >= 4:
        confirmations.append("buyer_breadth")
    if burst_60s >= 8:
        confirmations.append("burst_strength")
    if liq >= policy.heating_min_liq_usd and buys5m >= 10:
        confirmations.append("market_support")
    if attention >= policy.heating_min_attention:
        confirmations.append("attention_support")
    if x_mentions >= policy.heating_min_x_mentions and x_authors >= policy.heating_min_x_authors:
        confirmations.append("social_support")

    flow_confirmations = [item for item in confirmations if item in {"buyer_breadth", "burst_strength"}]
    quality_confirmations = [
        item
        for item in confirmations
        if item in {"tracked_wallet_flow", "kol_wallet_flow", "market_support", "social_support"}
    ]
    support_confirmations = [item for item in confirmations if item in {"attention_support", "social_support"}]
    core_sniper_met = (
        unique_10s >= policy.sniper_min_unique_10s
        and burst_10s >= policy.sniper_min_burst_10s
        and elite_score >= policy.sniper_min_elite
        and attention >= policy.sniper_min_attention
    )
    sniper_ready = (
        core_sniper_met
        and len(confirmations) >= policy.sniper_min_confirmations
        and bool(flow_confirmations)
        and bool(quality_confirmations)
    )
    sniper_fast_track = (
        sniper_ready
        and attention >= policy.sniper_fast_track_attention
        and len(confirmations) >= policy.sniper_fast_track_confirmations
    )
    route_confidence = min(
        1.0,
        (
            min(attention / max(policy.sniper_min_attention, 0.01), 1.2) * 0.24
            + min(max(elite_score, 0) / max(policy.sniper_min_elite, 1), 1.2) * 0.22
            + min(unique_10s / max(policy.sniper_min_unique_10s, 1), 1.4) * 0.16
            + min(burst_10s / max(policy.sniper_min_burst_10s, 1), 1.4) * 0.16
            + min(len(confirmations) / max(policy.sniper_fast_track_confirmations, 1), 1.2) * 0.12
            + (0.06 if flow_confirmations else 0.0)
            + (0.04 if quality_confirmations else 0.0)
        ),
    )

    if hard_fail_from_authority_checks:
        blockers.append("authority_hard_fail")
        sniper_blockers.append("authority_hard_fail")

    if unique_10s < policy.sniper_min_unique_10s:
        sniper_blockers.append(f"unique_10s<{policy.sniper_min_unique_10s}")
    if burst_10s < policy.sniper_min_burst_10s:
        sniper_blockers.append(f"burst_10s<{policy.sniper_min_burst_10s}")
    if elite_score < policy.sniper_min_elite:
        sniper_blockers.append(f"elite<{policy.sniper_min_elite}")
    if attention < policy.sniper_min_attention:
        sniper_blockers.append(f"sniper_attention<{policy.sniper_min_attention:.2f}")
    if len(confirmations) < policy.sniper_min_confirmations:
        sniper_blockers.append(f"sniper_confirmations<{policy.sniper_min_confirmations}")
    if not flow_confirmations:
        sniper_blockers.append("sniper_flow_confirmation_missing")
    if not quality_confirmations:
        sniper_blockers.append("sniper_quality_confirmation_missing")

    if (
        not blockers
        and sniper_ready
    ):
        route_tier = "sniper"
    elif not blockers and len(confirmations) >= policy.heating_min_confirmations and (
        attention >= policy.heating_min_attention
        or elite_score >= max(7, policy.sniper_min_elite - 1)
        or tracked_hits > 0
        or kol_hits > 0
    ) and (
        bool(flow_confirmations)
        or bool(quality_confirmations)
    ):
        route_tier = "heating_up"
        if core_sniper_met and not sniper_ready:
            blockers.extend(item for item in sniper_blockers if item not in blockers)
    else:
        if unique_10s < policy.sniper_min_unique_10s:
            blockers.append(f"unique_10s<{policy.sniper_min_unique_10s}")
        if burst_10s < policy.sniper_min_burst_10s:
            blockers.append(f"burst_10s<{policy.sniper_min_burst_10s}")
        if elite_score < policy.sniper_min_elite:
            blockers.append(f"elite<{policy.sniper_min_elite}")
        if attention < policy.heating_min_attention:
            blockers.append(f"attention<{policy.heating_min_attention:.2f}")
        if len(confirmations) < policy.heating_min_confirmations:
            blockers.append(f"route_confirmations<{policy.heating_min_confirmations}")
        if not flow_confirmations:
            blockers.append("route_flow_confirmation_missing")
        if not quality_confirmations:
            blockers.append("route_quality_confirmation_missing")

    age_bypass_eligible = False
    age_bypass_ttl_sec = 0
    age_bypass_reason = ""
    if route_tier == "sniper":
        age_bypass_eligible = True
        age_bypass_ttl_sec = policy.sniper_age_bypass_ttl_sec
        age_bypass_reason = "sniper_route"
    elif (
        route_tier == "heating_up"
        and core_sniper_met
        and route_confidence >= 0.70
        and support_confirmations
    ):
        age_bypass_eligible = True
        age_bypass_ttl_sec = policy.heating_age_bypass_ttl_sec
        age_bypass_reason = "near_sniper_route"

    return {
        "tier": route_tier,
        "confirmations": confirmations,
        "blockers": blockers,
        "sniper_blockers": sniper_blockers,
        "sniper_ready": sniper_ready,
        "sniper_fast_track": sniper_fast_track,
        "sniper_near_miss": bool(core_sniper_met and not sniper_ready),
        "route_confidence": route_confidence,
        "age_bypass_eligible": age_bypass_eligible,
        "age_bypass_ttl_sec": age_bypass_ttl_sec,
        "age_bypass_reason": age_bypass_reason,
        "policy": policy.as_dict(),
        "metrics": {
            "attention_score": attention,
            "elite_score": elite_score,
            "unique_10s": unique_10s,
            "burst_10s": burst_10s,
            "tracked_wallet_hits": tracked_hits,
            "kol_wallet_hits": kol_hits,
            "unique_buyers_5m": buyers_5m,
            "burst_count_60s": burst_60s,
            "liquidity_usd": liq,
            "txns_m5_buys": buys5m,
            "x_tweet_count": x_mentions,
            "x_unique_authors": x_authors,
        },
    }


def heating_delivery_decision(extra: dict[str, Any] | None) -> tuple[bool, list[str]]:
    payload = extra if isinstance(extra, dict) else {}
    route = payload.get("route_decision") if isinstance(payload.get("route_decision"), dict) else {}
    tier = str(route.get("tier") or "")
    confirmations = route.get("confirmations") if isinstance(route.get("confirmations"), list) else []
    blockers = route.get("blockers") if isinstance(route.get("blockers"), list) else []
    route_confidence = float(route.get("route_confidence") or 0.0)
    if tier == "sniper":
        return True, ["sniper_route", f"route_confidence:{route_confidence:.2f}", *confirmations]
    if tier == "heating_up" and confirmations and route_confidence >= 0.55:
        return True, [f"route_confidence:{route_confidence:.2f}", *confirmations]
    return False, blockers or ["route_not_heating_ready"]

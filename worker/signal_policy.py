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


def _first_positive_float(*sources: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            try:
                value = source.get(key)
                if value in (None, ""):
                    continue
                parsed = float(value)
            except Exception:
                continue
            if parsed > 0.0:
                return parsed
    return None


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
    social_support_min_mentions: int
    social_support_min_authors: int
    market_support_min_buys5m: int
    anti_wash_top_wallet_share: float
    anti_wash_unique_wallets_30s: int
    adversarial_max_sell_ratio_5m: float
    adversarial_max_vol_liq_ratio_5m: float
    adversarial_shallow_liq_usd: float
    adversarial_max_single_holder_ratio: float
    adversarial_min_volume_market_cap_ratio: float
    adversarial_social_min_author_ratio: float
    adversarial_social_min_mentions: int
    viral_social_min_mentions: int
    viral_social_min_authors: int
    viral_social_min_likes: int
    entry_chase_price_change_5m: float
    entry_chase_price_change_h1: float
    entry_chase_max_buy_sell_ratio: float
    entry_chase_min_liq_usd: float
    entry_confirm_breadth_min: int
    entry_confirm_buys5m_min: int

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
    strong_signal_discount_min_count: int
    low_quality_extra_min_count: int

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
    route_buyer_breadth_min: int
    route_burst_strength_min: int
    route_market_support_min_buys5m: int
    heating_delivery_min_confidence: float
    heating_delivery_min_confirmations: int
    adversarial_max_sell_ratio_5m: float
    adversarial_max_vol_liq_ratio_5m: float
    adversarial_shallow_liq_usd: float
    adversarial_flags_block_heating: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AttentionScoringPolicy:
    burst_weight_small_buy_sol: float
    burst_weight_small_value: int
    burst_weight_medium_buy_sol: float
    burst_weight_medium_value: int
    burst_weight_large_buy_sol: float
    burst_weight_large_value: int
    burst_weight_extreme_value: int
    local_buyers_primary_count: int
    local_buyers_primary_step: float
    local_buyers_secondary_step: float
    local_buyers_max_score: float
    local_burst_primary_count: int
    local_burst_primary_step: float
    local_burst_secondary_step: float
    local_burst_max_score: float
    local_buyers_15m_cap_count: int
    local_buyers_15m_step: float
    local_buyers_15m_max_score: float
    buyer_breadth_reason_min: int
    burst_reason_min: int
    buyer_breadth_15m_reason_min: int
    anti_wash_penalty: float
    anti_wash_multiplier: float
    acceleration_unique_3_min: int
    acceleration_unique_3_boost: float
    acceleration_unique_4_min: int
    acceleration_unique_4_boost: float
    acceleration_unique_5_min: int
    acceleration_unique_5_boost: float
    dexscreener_boost_threshold: int
    dexscreener_boost_score: float
    birdeye_trending_score: float
    pumpportal_burst_threshold: int
    pumpportal_burst_score: float
    tracked_wallet_step: float
    tracked_wallet_max_score: float
    kol_wallet_step: float
    kol_wallet_max_score: float
    narrative_step: float
    narrative_max_score: float
    x_local_gate_with_boost: float
    x_local_gate_with_birdeye: float
    x_local_gate_strong: float
    x_query_min_buyers_5m: int
    x_query_min_burst_60s: int
    x_mentions_threshold: int
    x_mentions_score: float
    x_authors_threshold: int
    x_authors_score: float
    x_likes_threshold: int
    x_likes_score: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreatorScorePolicy:
    base_score: float
    deploys_24h_penalty_threshold: int
    deploys_24h_penalty: float
    deploys_lifetime_penalty_threshold: int
    deploys_lifetime_penalty: float
    funded_by_cluster_penalty: float
    prior_profitable_bonus: float
    wallet_age_bonus_days: float
    wallet_age_bonus: float
    low_frequency_deploys_24h_max: int
    low_frequency_deploys_lifetime_max: int
    low_frequency_bonus: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EliteScorePolicy:
    hard_fail_score: int
    capital_large_buy_sol: float
    capital_large_score: int
    capital_medium_buy_sol: float
    capital_medium_score: int
    capital_small_buy_sol: float
    capital_small_score: int
    velocity_high_unique_10s: int
    velocity_high_score: int
    velocity_mid_unique_10s: int
    velocity_mid_score: int
    velocity_low_unique_10s: int
    velocity_low_score: int
    concentrated_top_wallet_share: float
    concentrated_unique_wallets_30s_max: int
    concentrated_penalty: int
    broad_distribution_unique_wallets_30s_min: int
    broad_distribution_bonus: int
    safety_liquidity_bonus_usd: float
    safety_liquidity_bonus: int
    safety_locked_bonus: int
    decay_watch_attention_min: float
    decay_window_sec: int
    decay_burst_drop_multiplier: float
    decay_liquidity_drop_multiplier: float
    decay_blacklist_ttl_sec: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscordPresentationPolicy:
    score_band_high: float
    score_band_constructive: float
    score_band_mixed: float
    summary_risk_elevated: float
    summary_dex_attention_high: float
    summary_dex_risk_low: float
    summary_attention_high: float
    summary_watch_risk: float
    summary_dex_attention_constructive: float
    flow_bias_buy_imbalance: float
    flow_bias_sell_imbalance: float
    momentum_confirm_attention: float
    momentum_confirm_price_change_5m: float
    momentum_early_attention: float
    risk_band_low_max: float
    risk_band_mixed_max: float
    risk_band_elevated_max: float
    confidence_dot_green_min: float
    confidence_dot_yellow_min: float
    confidence_band_high_min: float
    confidence_band_strong_min: float
    confidence_band_moderate_min: float
    risk_alert_threshold: float
    breakout_attention_min: float
    breakout_risk_max: float
    setup_attention_min: float
    candidate_header_breakout_attention_min: float
    candidate_header_setup_attention_min: float
    candidate_header_setup_risk_max: float
    promoted_header_strong_min: float
    conviction_confirmed_attention_min: float
    conviction_confirmed_risk_max: float
    conviction_strong_attention_min: float
    conviction_early_attention_min: float
    quality_tier_a_elite_min: int
    quality_tier_a_attention_min: float
    quality_tier_a_risk_max: float
    quality_tier_b_elite_min: int
    quality_tier_b_attention_min: float
    elite_band_elite_min: int
    elite_band_strong_min: int
    elite_band_developing_min: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateLifecyclePolicy:
    liq_unknown_bypass_attention_min: float
    liq_unknown_bypass_unique_buyers_5m_min: int
    candidate_stage_b_attention_min: float
    candidate_stage_b_unique_buyers_5m_min: int
    candidate_stage_c_confidence_min: float
    candidate_stage_c_creator_min: float
    recheck_stop_max_age_days: int
    recheck_stop_never_crossed_days: int
    recheck_stop_never_crossed_confidence_min: float
    heating_review_confidence_min: float
    heating_review_confidence_max: float

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
        social_support_min_mentions=_env_int("SIGNAL_ENGINE_CANDIDATE_SOCIAL_SUPPORT_MIN_MENTIONS", 5),
        social_support_min_authors=_env_int("SIGNAL_ENGINE_CANDIDATE_SOCIAL_SUPPORT_MIN_AUTHORS", 3),
        market_support_min_buys5m=_env_int("SIGNAL_ENGINE_CANDIDATE_MARKET_SUPPORT_MIN_BUYS5M", 8),
        anti_wash_top_wallet_share=_env_float("SIGNAL_ENGINE_ANTI_WASH_TOP_WALLET_SHARE", 0.70),
        anti_wash_unique_wallets_30s=_env_int("SIGNAL_ENGINE_ANTI_WASH_MAX_UNIQUE_WALLETS_30S", 2),
        adversarial_max_sell_ratio_5m=_env_float("SIGNAL_ENGINE_CANDIDATE_ADVERSARIAL_MAX_SELL_RATIO_5M", 1.25),
        adversarial_max_vol_liq_ratio_5m=_env_float("SIGNAL_ENGINE_CANDIDATE_ADVERSARIAL_MAX_VOL_LIQ_RATIO_5M", 4.0),
        adversarial_shallow_liq_usd=_env_float("SIGNAL_ENGINE_CANDIDATE_ADVERSARIAL_SHALLOW_LIQ_USD", 10000.0),
        adversarial_max_single_holder_ratio=_env_float("SIGNAL_ENGINE_CANDIDATE_ADVERSARIAL_MAX_SINGLE_HOLDER_RATIO", 0.035),
        adversarial_min_volume_market_cap_ratio=_env_float("SIGNAL_ENGINE_CANDIDATE_ADVERSARIAL_MIN_VOLUME_MARKET_CAP_RATIO", 0.80),
        adversarial_social_min_author_ratio=_env_float("SIGNAL_ENGINE_CANDIDATE_ADVERSARIAL_SOCIAL_MIN_AUTHOR_RATIO", 0.35),
        adversarial_social_min_mentions=_env_int("SIGNAL_ENGINE_CANDIDATE_ADVERSARIAL_SOCIAL_MIN_MENTIONS", 8),
        viral_social_min_mentions=_env_int("SIGNAL_ENGINE_CANDIDATE_VIRAL_SOCIAL_MIN_MENTIONS", 8),
        viral_social_min_authors=_env_int("SIGNAL_ENGINE_CANDIDATE_VIRAL_SOCIAL_MIN_AUTHORS", 4),
        viral_social_min_likes=_env_int("SIGNAL_ENGINE_CANDIDATE_VIRAL_SOCIAL_MIN_LIKES", 30),
        entry_chase_price_change_5m=_env_float("SIGNAL_ENGINE_ENTRY_CHASE_PRICE_CHANGE_5M", 45.0),
        entry_chase_price_change_h1=_env_float("SIGNAL_ENGINE_ENTRY_CHASE_PRICE_CHANGE_H1", 140.0),
        entry_chase_max_buy_sell_ratio=_env_float("SIGNAL_ENGINE_ENTRY_CHASE_MAX_BUY_SELL_RATIO", 1.35),
        entry_chase_min_liq_usd=_env_float("SIGNAL_ENGINE_ENTRY_CHASE_MIN_LIQ_USD", 15000.0),
        entry_confirm_breadth_min=_env_int("SIGNAL_ENGINE_ENTRY_CONFIRM_BREADTH_MIN", 5),
        entry_confirm_buys5m_min=_env_int("SIGNAL_ENGINE_ENTRY_CONFIRM_BUYS5M_MIN", 14),
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
        strong_signal_discount_min_count=_env_int("SIGNAL_ENGINE_PROMOTED_STRONG_SIGNAL_DISCOUNT_MIN_COUNT", 4),
        low_quality_extra_min_count=_env_int("SIGNAL_ENGINE_PROMOTED_LOW_QUALITY_EXTRA_MIN_COUNT", 1),
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
        route_buyer_breadth_min=_env_int("SIGNAL_ENGINE_ROUTE_BUYER_BREADTH_MIN", 4),
        route_burst_strength_min=_env_int("SIGNAL_ENGINE_ROUTE_BURST_STRENGTH_MIN", 8),
        route_market_support_min_buys5m=_env_int("SIGNAL_ENGINE_ROUTE_MARKET_SUPPORT_MIN_BUYS5M", 10),
        heating_delivery_min_confidence=_env_float("SIGNAL_ENGINE_HEATING_DELIVERY_MIN_CONFIDENCE", 0.55),
        heating_delivery_min_confirmations=_env_int("SIGNAL_ENGINE_HEATING_DELIVERY_MIN_CONFIRMATIONS", 3),
        adversarial_max_sell_ratio_5m=_env_float("SIGNAL_ENGINE_ROUTE_ADVERSARIAL_MAX_SELL_RATIO_5M", 1.2),
        adversarial_max_vol_liq_ratio_5m=_env_float("SIGNAL_ENGINE_ROUTE_ADVERSARIAL_MAX_VOL_LIQ_RATIO_5M", 4.0),
        adversarial_shallow_liq_usd=_env_float("SIGNAL_ENGINE_ROUTE_ADVERSARIAL_SHALLOW_LIQ_USD", 12000.0),
        adversarial_flags_block_heating=_env_int("SIGNAL_ENGINE_ROUTE_ADVERSARIAL_FLAGS_BLOCK_HEATING", 2),
    )


def attention_scoring_policy() -> AttentionScoringPolicy:
    return AttentionScoringPolicy(
        burst_weight_small_buy_sol=_env_float("SIGNAL_ENGINE_ATTN_BURST_WEIGHT_SMALL_BUY_SOL", 0.2),
        burst_weight_small_value=_env_int("SIGNAL_ENGINE_ATTN_BURST_WEIGHT_SMALL_VALUE", 1),
        burst_weight_medium_buy_sol=_env_float("SIGNAL_ENGINE_ATTN_BURST_WEIGHT_MEDIUM_BUY_SOL", 1.0),
        burst_weight_medium_value=_env_int("SIGNAL_ENGINE_ATTN_BURST_WEIGHT_MEDIUM_VALUE", 2),
        burst_weight_large_buy_sol=_env_float("SIGNAL_ENGINE_ATTN_BURST_WEIGHT_LARGE_BUY_SOL", 3.0),
        burst_weight_large_value=_env_int("SIGNAL_ENGINE_ATTN_BURST_WEIGHT_LARGE_VALUE", 3),
        burst_weight_extreme_value=_env_int("SIGNAL_ENGINE_ATTN_BURST_WEIGHT_EXTREME_VALUE", 5),
        # Higher values demand broader local participation before attention ramps.
        local_buyers_primary_count=_env_int("SIGNAL_ENGINE_ATTN_LOCAL_BUYERS_PRIMARY_COUNT", 4),
        local_buyers_primary_step=_env_float("SIGNAL_ENGINE_ATTN_LOCAL_BUYERS_PRIMARY_STEP", 0.045),
        local_buyers_secondary_step=_env_float("SIGNAL_ENGINE_ATTN_LOCAL_BUYERS_SECONDARY_STEP", 0.02),
        local_buyers_max_score=_env_float("SIGNAL_ENGINE_ATTN_LOCAL_BUYERS_MAX_SCORE", 0.32),
        # Burst scoring controls how quickly short-window velocity lifts the score.
        local_burst_primary_count=_env_int("SIGNAL_ENGINE_ATTN_LOCAL_BURST_PRIMARY_COUNT", 8),
        local_burst_primary_step=_env_float("SIGNAL_ENGINE_ATTN_LOCAL_BURST_PRIMARY_STEP", 0.015),
        local_burst_secondary_step=_env_float("SIGNAL_ENGINE_ATTN_LOCAL_BURST_SECONDARY_STEP", 0.0075),
        local_burst_max_score=_env_float("SIGNAL_ENGINE_ATTN_LOCAL_BURST_MAX_SCORE", 0.24),
        # 15m breadth should help, but not overpower the live fast path.
        local_buyers_15m_cap_count=_env_int("SIGNAL_ENGINE_ATTN_LOCAL_BUYERS_15M_CAP_COUNT", 18),
        local_buyers_15m_step=_env_float("SIGNAL_ENGINE_ATTN_LOCAL_BUYERS_15M_STEP", 0.012),
        local_buyers_15m_max_score=_env_float("SIGNAL_ENGINE_ATTN_LOCAL_BUYERS_15M_MAX_SCORE", 0.22),
        buyer_breadth_reason_min=_env_int("SIGNAL_ENGINE_ATTN_REASON_MIN_BUYERS_5M", 3),
        burst_reason_min=_env_int("SIGNAL_ENGINE_ATTN_REASON_MIN_BURST_60S", 8),
        buyer_breadth_15m_reason_min=_env_int("SIGNAL_ENGINE_ATTN_REASON_MIN_BUYERS_15M", 8),
        # Higher penalties/multipliers make anti-wash suppression stricter.
        anti_wash_penalty=_env_float("SIGNAL_ENGINE_ATTN_ANTI_WASH_PENALTY", 0.30),
        anti_wash_multiplier=_env_float("SIGNAL_ENGINE_ATTN_ANTI_WASH_MULTIPLIER", 0.70),
        # Lower boosts make acceleration more conservative; higher boosts fire earlier.
        acceleration_unique_3_min=_env_int("SIGNAL_ENGINE_ATTN_ACCEL_UNIQUE3_MIN", 3),
        acceleration_unique_3_boost=_env_float("SIGNAL_ENGINE_ATTN_ACCEL_UNIQUE3_BOOST", 0.10),
        acceleration_unique_4_min=_env_int("SIGNAL_ENGINE_ATTN_ACCEL_UNIQUE4_MIN", 4),
        acceleration_unique_4_boost=_env_float("SIGNAL_ENGINE_ATTN_ACCEL_UNIQUE4_BOOST", 0.15),
        acceleration_unique_5_min=_env_int("SIGNAL_ENGINE_ATTN_ACCEL_UNIQUE5_MIN", 5),
        acceleration_unique_5_boost=_env_float("SIGNAL_ENGINE_ATTN_ACCEL_UNIQUE5_BOOST", 0.20),
        dexscreener_boost_threshold=_env_int("SIGNAL_ENGINE_ATTN_DEXSCREENER_BOOST_THRESHOLD", 1),
        dexscreener_boost_score=_env_float("SIGNAL_ENGINE_ATTN_DEXSCREENER_BOOST_SCORE", 0.20),
        birdeye_trending_score=_env_float("SIGNAL_ENGINE_ATTN_BIRDEYE_TRENDING_SCORE", 0.10),
        pumpportal_burst_threshold=_env_int("SIGNAL_ENGINE_ATTN_PUMPPORTAL_BURST_THRESHOLD", 20),
        pumpportal_burst_score=_env_float("SIGNAL_ENGINE_ATTN_PUMPPORTAL_BURST_SCORE", 0.20),
        tracked_wallet_step=_env_float("SIGNAL_ENGINE_ATTN_TRACKED_WALLET_STEP", 0.05),
        tracked_wallet_max_score=_env_float("SIGNAL_ENGINE_ATTN_TRACKED_WALLET_MAX_SCORE", 0.15),
        kol_wallet_step=_env_float("SIGNAL_ENGINE_ATTN_KOL_WALLET_STEP", 0.10),
        kol_wallet_max_score=_env_float("SIGNAL_ENGINE_ATTN_KOL_WALLET_MAX_SCORE", 0.20),
        narrative_step=_env_float("SIGNAL_ENGINE_ATTN_NARRATIVE_STEP", 0.05),
        narrative_max_score=_env_float("SIGNAL_ENGINE_ATTN_NARRATIVE_MAX_SCORE", 0.10),
        # Lower X gates query social earlier; higher gates keep the hot path cheaper.
        x_local_gate_with_boost=_env_float("SIGNAL_ENGINE_ATTN_X_LOCAL_GATE_WITH_BOOST", 0.25),
        x_local_gate_with_birdeye=_env_float("SIGNAL_ENGINE_ATTN_X_LOCAL_GATE_WITH_BIRDEYE", 0.20),
        x_local_gate_strong=_env_float("SIGNAL_ENGINE_ATTN_X_LOCAL_GATE_STRONG", 0.32),
        x_query_min_buyers_5m=_env_int("SIGNAL_ENGINE_ATTN_X_QUERY_MIN_BUYERS_5M", 4),
        x_query_min_burst_60s=_env_int("SIGNAL_ENGINE_ATTN_X_QUERY_MIN_BURST_60S", 10),
        x_mentions_threshold=_env_int("SIGNAL_ENGINE_ATTN_X_MENTIONS_THRESHOLD", 3),
        x_mentions_score=_env_float("SIGNAL_ENGINE_ATTN_X_MENTIONS_SCORE", 0.05),
        x_authors_threshold=_env_int("SIGNAL_ENGINE_ATTN_X_AUTHORS_THRESHOLD", 3),
        x_authors_score=_env_float("SIGNAL_ENGINE_ATTN_X_AUTHORS_SCORE", 0.05),
        x_likes_threshold=_env_int("SIGNAL_ENGINE_ATTN_X_LIKES_THRESHOLD", 20),
        x_likes_score=_env_float("SIGNAL_ENGINE_ATTN_X_LIKES_SCORE", 0.05),
    )


def creator_score_policy() -> CreatorScorePolicy:
    return CreatorScorePolicy(
        # Raising the base makes unknown creators less punitive by default.
        base_score=_env_float("SIGNAL_ENGINE_CREATOR_BASE_SCORE", 0.50),
        deploys_24h_penalty_threshold=_env_int("SIGNAL_ENGINE_CREATOR_DEPLOYS_24H_PENALTY_THRESHOLD", 5),
        deploys_24h_penalty=_env_float("SIGNAL_ENGINE_CREATOR_DEPLOYS_24H_PENALTY", 0.30),
        deploys_lifetime_penalty_threshold=_env_int("SIGNAL_ENGINE_CREATOR_DEPLOYS_LIFETIME_PENALTY_THRESHOLD", 2),
        deploys_lifetime_penalty=_env_float("SIGNAL_ENGINE_CREATOR_DEPLOYS_LIFETIME_PENALTY", 0.20),
        funded_by_cluster_penalty=_env_float("SIGNAL_ENGINE_CREATOR_FUNDED_BY_CLUSTER_PENALTY", 0.40),
        prior_profitable_bonus=_env_float("SIGNAL_ENGINE_CREATOR_PRIOR_PROFITABLE_BONUS", 0.30),
        wallet_age_bonus_days=_env_float("SIGNAL_ENGINE_CREATOR_WALLET_AGE_BONUS_DAYS", 30.0),
        wallet_age_bonus=_env_float("SIGNAL_ENGINE_CREATOR_WALLET_AGE_BONUS", 0.20),
        low_frequency_deploys_24h_max=_env_int("SIGNAL_ENGINE_CREATOR_LOW_FREQ_DEPLOYS_24H_MAX", 1),
        low_frequency_deploys_lifetime_max=_env_int("SIGNAL_ENGINE_CREATOR_LOW_FREQ_DEPLOYS_LIFETIME_MAX", 5),
        low_frequency_bonus=_env_float("SIGNAL_ENGINE_CREATOR_LOW_FREQ_BONUS", 0.20),
    )


def elite_score_policy() -> EliteScorePolicy:
    return EliteScorePolicy(
        hard_fail_score=_env_int("SIGNAL_ENGINE_ELITE_HARD_FAIL_SCORE", -999),
        capital_large_buy_sol=_env_float("SIGNAL_ENGINE_ELITE_CAPITAL_LARGE_BUY_SOL", 3.0),
        capital_large_score=_env_int("SIGNAL_ENGINE_ELITE_CAPITAL_LARGE_SCORE", 5),
        capital_medium_buy_sol=_env_float("SIGNAL_ENGINE_ELITE_CAPITAL_MEDIUM_BUY_SOL", 1.0),
        capital_medium_score=_env_int("SIGNAL_ENGINE_ELITE_CAPITAL_MEDIUM_SCORE", 3),
        capital_small_buy_sol=_env_float("SIGNAL_ENGINE_ELITE_CAPITAL_SMALL_BUY_SOL", 0.2),
        capital_small_score=_env_int("SIGNAL_ENGINE_ELITE_CAPITAL_SMALL_SCORE", 2),
        velocity_high_unique_10s=_env_int("SIGNAL_ENGINE_ELITE_VELOCITY_HIGH_UNIQUE_10S", 5),
        velocity_high_score=_env_int("SIGNAL_ENGINE_ELITE_VELOCITY_HIGH_SCORE", 5),
        velocity_mid_unique_10s=_env_int("SIGNAL_ENGINE_ELITE_VELOCITY_MID_UNIQUE_10S", 4),
        velocity_mid_score=_env_int("SIGNAL_ENGINE_ELITE_VELOCITY_MID_SCORE", 3),
        velocity_low_unique_10s=_env_int("SIGNAL_ENGINE_ELITE_VELOCITY_LOW_UNIQUE_10S", 3),
        velocity_low_score=_env_int("SIGNAL_ENGINE_ELITE_VELOCITY_LOW_SCORE", 2),
        concentrated_top_wallet_share=_env_float("SIGNAL_ENGINE_ELITE_CONCENTRATED_TOP_WALLET_SHARE", 0.70),
        concentrated_unique_wallets_30s_max=_env_int("SIGNAL_ENGINE_ELITE_CONCENTRATED_UNIQUE_WALLETS_30S_MAX", 2),
        concentrated_penalty=_env_int("SIGNAL_ENGINE_ELITE_CONCENTRATED_PENALTY", -3),
        broad_distribution_unique_wallets_30s_min=_env_int("SIGNAL_ENGINE_ELITE_BROAD_DISTRIBUTION_UNIQUE_WALLETS_30S_MIN", 4),
        broad_distribution_bonus=_env_int("SIGNAL_ENGINE_ELITE_BROAD_DISTRIBUTION_BONUS", 1),
        safety_liquidity_bonus_usd=_env_float("SIGNAL_ENGINE_ELITE_SAFETY_LIQUIDITY_BONUS_USD", 50000.0),
        safety_liquidity_bonus=_env_int("SIGNAL_ENGINE_ELITE_SAFETY_LIQUIDITY_BONUS", 1),
        safety_locked_bonus=_env_int("SIGNAL_ENGINE_ELITE_SAFETY_LOCKED_BONUS", 1),
        decay_watch_attention_min=_env_float("SIGNAL_ENGINE_ELITE_DECAY_WATCH_ATTENTION_MIN", 0.35),
        decay_window_sec=_env_int("SIGNAL_ENGINE_ELITE_DECAY_WINDOW_SECONDS", 20),
        decay_burst_drop_multiplier=_env_float("SIGNAL_ENGINE_ELITE_DECAY_BURST_DROP_MULTIPLIER", 0.50),
        decay_liquidity_drop_multiplier=_env_float("SIGNAL_ENGINE_ELITE_DECAY_LIQUIDITY_DROP_MULTIPLIER", 0.75),
        decay_blacklist_ttl_sec=_env_int("SIGNAL_ENGINE_ELITE_DECAY_BLACKLIST_TTL_SECONDS", 600),
    )


def discord_presentation_policy() -> DiscordPresentationPolicy:
    return DiscordPresentationPolicy(
        score_band_high=_env_float("SIGNAL_ENGINE_DISCORD_SCORE_BAND_HIGH", 0.80),
        score_band_constructive=_env_float("SIGNAL_ENGINE_DISCORD_SCORE_BAND_CONSTRUCTIVE", 0.60),
        score_band_mixed=_env_float("SIGNAL_ENGINE_DISCORD_SCORE_BAND_MIXED", 0.40),
        summary_risk_elevated=_env_float("SIGNAL_ENGINE_DISCORD_SUMMARY_RISK_ELEVATED", 0.70),
        summary_dex_attention_high=_env_float("SIGNAL_ENGINE_DISCORD_SUMMARY_DEX_ATTENTION_HIGH", 0.80),
        summary_dex_risk_low=_env_float("SIGNAL_ENGINE_DISCORD_SUMMARY_DEX_RISK_LOW", 0.20),
        summary_attention_high=_env_float("SIGNAL_ENGINE_DISCORD_SUMMARY_ATTENTION_HIGH", 0.70),
        summary_watch_risk=_env_float("SIGNAL_ENGINE_DISCORD_SUMMARY_WATCH_RISK", 0.50),
        summary_dex_attention_constructive=_env_float("SIGNAL_ENGINE_DISCORD_SUMMARY_DEX_ATTENTION_CONSTRUCTIVE", 0.50),
        flow_bias_buy_imbalance=_env_float("SIGNAL_ENGINE_DISCORD_FLOW_BIAS_BUY_IMBALANCE", 0.20),
        flow_bias_sell_imbalance=_env_float("SIGNAL_ENGINE_DISCORD_FLOW_BIAS_SELL_IMBALANCE", -0.20),
        momentum_confirm_attention=_env_float("SIGNAL_ENGINE_DISCORD_MOMENTUM_CONFIRM_ATTENTION", 0.80),
        momentum_confirm_price_change_5m=_env_float("SIGNAL_ENGINE_DISCORD_MOMENTUM_CONFIRM_PRICE_CHANGE_5M", 20.0),
        momentum_early_attention=_env_float("SIGNAL_ENGINE_DISCORD_MOMENTUM_EARLY_ATTENTION", 0.60),
        risk_band_low_max=_env_float("SIGNAL_ENGINE_DISCORD_RISK_BAND_LOW_MAX", 0.20),
        risk_band_mixed_max=_env_float("SIGNAL_ENGINE_DISCORD_RISK_BAND_MIXED_MAX", 0.45),
        risk_band_elevated_max=_env_float("SIGNAL_ENGINE_DISCORD_RISK_BAND_ELEVATED_MAX", 0.70),
        confidence_dot_green_min=_env_float("SIGNAL_ENGINE_DISCORD_CONFIDENCE_DOT_GREEN_MIN", 0.80),
        confidence_dot_yellow_min=_env_float("SIGNAL_ENGINE_DISCORD_CONFIDENCE_DOT_YELLOW_MIN", 0.45),
        confidence_band_high_min=_env_float("SIGNAL_ENGINE_DISCORD_CONFIDENCE_BAND_HIGH_MIN", 0.80),
        confidence_band_strong_min=_env_float("SIGNAL_ENGINE_DISCORD_CONFIDENCE_BAND_STRONG_MIN", 0.65),
        confidence_band_moderate_min=_env_float("SIGNAL_ENGINE_DISCORD_CONFIDENCE_BAND_MODERATE_MIN", 0.45),
        risk_alert_threshold=_env_float("SIGNAL_ENGINE_DISCORD_RISK_ALERT_THRESHOLD", 0.70),
        breakout_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_BREAKOUT_ATTENTION_MIN", 0.80),
        breakout_risk_max=_env_float("SIGNAL_ENGINE_DISCORD_BREAKOUT_RISK_MAX", 0.35),
        setup_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_SETUP_ATTENTION_MIN", 0.55),
        candidate_header_breakout_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_CANDIDATE_HEADER_BREAKOUT_ATTENTION_MIN", 0.85),
        candidate_header_setup_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_CANDIDATE_HEADER_SETUP_ATTENTION_MIN", 0.70),
        candidate_header_setup_risk_max=_env_float("SIGNAL_ENGINE_DISCORD_CANDIDATE_HEADER_SETUP_RISK_MAX", 0.50),
        promoted_header_strong_min=_env_float("SIGNAL_ENGINE_DISCORD_PROMOTED_HEADER_STRONG_MIN", 0.80),
        conviction_confirmed_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_CONVICTION_CONFIRMED_ATTENTION_MIN", 0.85),
        conviction_confirmed_risk_max=_env_float("SIGNAL_ENGINE_DISCORD_CONVICTION_CONFIRMED_RISK_MAX", 0.15),
        conviction_strong_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_CONVICTION_STRONG_ATTENTION_MIN", 0.80),
        conviction_early_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_CONVICTION_EARLY_ATTENTION_MIN", 0.65),
        quality_tier_a_elite_min=_env_int("SIGNAL_ENGINE_DISCORD_QUALITY_TIER_A_ELITE_MIN", 10),
        quality_tier_a_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_QUALITY_TIER_A_ATTENTION_MIN", 0.75),
        quality_tier_a_risk_max=_env_float("SIGNAL_ENGINE_DISCORD_QUALITY_TIER_A_RISK_MAX", 0.30),
        quality_tier_b_elite_min=_env_int("SIGNAL_ENGINE_DISCORD_QUALITY_TIER_B_ELITE_MIN", 7),
        quality_tier_b_attention_min=_env_float("SIGNAL_ENGINE_DISCORD_QUALITY_TIER_B_ATTENTION_MIN", 0.55),
        elite_band_elite_min=_env_int("SIGNAL_ENGINE_DISCORD_ELITE_BAND_ELITE_MIN", 10),
        elite_band_strong_min=_env_int("SIGNAL_ENGINE_DISCORD_ELITE_BAND_STRONG_MIN", 8),
        elite_band_developing_min=_env_int("SIGNAL_ENGINE_DISCORD_ELITE_BAND_DEVELOPING_MIN", 5),
    )


def candidate_lifecycle_policy() -> CandidateLifecyclePolicy:
    return CandidateLifecyclePolicy(
        liq_unknown_bypass_attention_min=_env_float("SIGNAL_ENGINE_LIQ_UNKNOWN_BYPASS_ATTENTION_MIN", 0.55),
        liq_unknown_bypass_unique_buyers_5m_min=_env_int("SIGNAL_ENGINE_LIQ_UNKNOWN_BYPASS_UNIQUE_BUYERS_5M_MIN", 4),
        candidate_stage_b_attention_min=_env_float("SIGNAL_ENGINE_CANDIDATE_STAGE_B_ATTENTION_MIN", 0.25),
        candidate_stage_b_unique_buyers_5m_min=_env_int("SIGNAL_ENGINE_CANDIDATE_STAGE_B_UNIQUE_BUYERS_5M_MIN", 5),
        candidate_stage_c_confidence_min=_env_float("SIGNAL_ENGINE_CANDIDATE_STAGE_C_CONFIDENCE_MIN", 0.50),
        candidate_stage_c_creator_min=_env_float("SIGNAL_ENGINE_CANDIDATE_STAGE_C_CREATOR_MIN", 0.50),
        recheck_stop_max_age_days=_env_int("SIGNAL_ENGINE_RECHECK_STOP_MAX_AGE_DAYS", 30),
        recheck_stop_never_crossed_days=_env_int("SIGNAL_ENGINE_RECHECK_STOP_NEVER_CROSSED_DAYS", 7),
        recheck_stop_never_crossed_confidence_min=_env_float("SIGNAL_ENGINE_RECHECK_STOP_NEVER_CROSSED_CONFIDENCE_MIN", 0.40),
        heating_review_confidence_min=_env_float("SIGNAL_ENGINE_HEATING_REVIEW_CONFIDENCE_MIN", 0.55),
        heating_review_confidence_max=_env_float("SIGNAL_ENGINE_HEATING_REVIEW_CONFIDENCE_MAX", 0.80),
    )


def market_quality_thresholds_for_age(age_min: float) -> MarketQualityThresholds:
    if age_min < 2.0:
        return MarketQualityThresholds(
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_EARLY_MIN_LIQ", 20000.0),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_EARLY_MIN_VOL5M", 12000.0),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_EARLY_MIN_BUYS5M", 20.0),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_EARLY_MAX_SELL_RATIO5M", 1.3),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_EARLY_MAX_VOL_LIQ_RATIO5M", 4.0),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_EARLY_MAX_PRICE_DROP5M", -8.0),
        )
    if age_min < 10.0:
        return MarketQualityThresholds(
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MID_MIN_LIQ", 12000.0),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MID_MIN_VOL5M", 7000.0),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MID_MIN_BUYS5M", 12.0),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MID_MAX_SELL_RATIO5M", 1.6),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MID_MAX_VOL_LIQ_RATIO5M", 6.0),
            _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MID_MAX_PRICE_DROP5M", -12.0),
        )
    return MarketQualityThresholds(
        _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MATURE_MIN_LIQ", 8000.0),
        _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MATURE_MIN_VOL5M", 5000.0),
        _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MATURE_MIN_BUYS5M", 8.0),
        _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MATURE_MAX_SELL_RATIO5M", 2.0),
        _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MATURE_MAX_VOL_LIQ_RATIO5M", 8.0),
        _env_float("SIGNAL_ENGINE_MARKET_QUALITY_MATURE_MAX_PRICE_DROP5M", -18.0),
    )


def dex_accumulation_watch_signal(
    metrics: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> bool:
    payload = metrics if isinstance(metrics, dict) else {}
    summary = dex_summary if isinstance(dex_summary, dict) else {}

    age_min = _first_positive_float(summary, payload, keys=("age_minutes",))
    liq = _first_positive_float(summary, payload, keys=("liquidity_usd", "liquidity"))
    vol5m = _first_positive_float(summary, payload, keys=("volume_m5", "volume_m5_usd", "volume_5m"))
    market_cap = _first_positive_float(summary, payload, keys=("market_cap_usd", "market_cap", "fdv"))
    try:
        buys5m = int(summary.get("txns_m5_buys") or summary.get("buys_5m") or payload.get("txns_m5_buys") or payload.get("buys_5m") or 0)
    except Exception:
        buys5m = 0
    try:
        sells5m = int(summary.get("txns_m5_sells") or summary.get("sells_5m") or payload.get("txns_m5_sells") or payload.get("sells_5m") or 0)
    except Exception:
        sells5m = 0
    try:
        chg5m = float(summary.get("price_change_m5") or summary.get("price_change_5m") or payload.get("price_change_m5") or payload.get("price_change_5m") or 0.0)
    except Exception:
        chg5m = 0.0
    repeat_count = int(payload.get("dex_scan_repeat_count") or 0)
    try:
        volume_delta = float(payload.get("dex_scan_volume_delta_5m") or 0.0)
    except Exception:
        volume_delta = 0.0
    persistent = bool(payload.get("dex_scan_persistent")) or repeat_count >= 2
    independent_flow = bool(payload.get("independent_flow_confirmed"))
    sources = payload.get("discovery_sources") if isinstance(payload.get("discovery_sources"), list) else []
    credible_source = bool(payload.get("community_takeover")) or "community_takeover" in sources
    sell_ratio = sells5m / max(1, buys5m)

    return (
        age_min is not None
        and age_min >= 10.0
        and persistent
        and liq is not None
        and liq >= 25_000.0
        and vol5m is not None
        and vol5m >= 1_000.0
        and buys5m >= 12
        and sell_ratio <= 1.25
        and chg5m >= -18.0
        and market_cap is not None
        and 50_000.0 <= market_cap <= 5_000_000.0
        and (independent_flow or volume_delta >= 500.0 or credible_source)
    )


def viral_theme_dex_momentum_signal(
    metrics: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> bool:
    payload = metrics if isinstance(metrics, dict) else {}
    summary = dex_summary if isinstance(dex_summary, dict) else {}
    viral_hits = payload.get("viral_theme_hits") if isinstance(payload.get("viral_theme_hits"), list) else []
    if not viral_hits:
        return False

    liq = _first_positive_float(summary, payload, keys=("liquidity_usd", "liquidity"))
    vol5m = _first_positive_float(summary, payload, keys=("volume_m5", "volume_m5_usd", "volume_5m"))
    market_cap = _first_positive_float(summary, payload, keys=("market_cap_usd", "market_cap", "fdv"))
    try:
        buys5m = int(summary.get("txns_m5_buys") or summary.get("buys_5m") or payload.get("txns_m5_buys") or payload.get("buys_5m") or 0)
    except Exception:
        buys5m = 0
    try:
        sells5m = int(summary.get("txns_m5_sells") or summary.get("sells_5m") or payload.get("txns_m5_sells") or payload.get("sells_5m") or 0)
    except Exception:
        sells5m = 0
    try:
        price_change_m5 = float(summary.get("price_change_m5") or summary.get("price_change_5m") or payload.get("price_change_m5") or payload.get("price_change_5m") or 0.0)
    except Exception:
        price_change_m5 = 0.0
    try:
        price_change_h1 = float(summary.get("price_change_h1") or summary.get("price_change_1h") or payload.get("price_change_h1") or payload.get("price_change_1h") or 0.0)
    except Exception:
        price_change_h1 = 0.0

    buy_sell_ratio = float(buys5m) / float(max(sells5m, 1)) if buys5m > 0 else 0.0
    sell_buy_ratio = float(sells5m) / float(max(buys5m, 1)) if buys5m > 0 else 0.0
    repeat_count = int(payload.get("dex_scan_repeat_count") or 0)
    sources = payload.get("discovery_sources") if isinstance(payload.get("discovery_sources"), list) else []
    source_supported = bool(
        payload.get("community_takeover")
        or payload.get("dex_scan_persistent")
        or repeat_count >= 2
        or {"community_takeover", "token_profile", "token_boost_top", "token_boost_latest"} & set(sources)
    )

    return (
        liq is not None
        and liq >= 25_000.0
        and vol5m is not None
        and vol5m >= 8_000.0
        and buys5m >= 20
        and sell_buy_ratio <= 0.85
        and buy_sell_ratio >= 1.5
        and (price_change_m5 >= 4.0 or price_change_h1 >= 18.0)
        and market_cap is not None
        and 50_000.0 <= market_cap <= 8_000_000.0
        and source_supported
    )


def dormant_revival_watch_signal(
    metrics: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> bool:
    payload = metrics if isinstance(metrics, dict) else {}
    summary = dex_summary if isinstance(dex_summary, dict) else {}

    age_min = _first_positive_float(summary, payload, keys=("age_minutes",))
    liq = _first_positive_float(summary, payload, keys=("liquidity_usd", "liquidity"))
    vol5m = _first_positive_float(summary, payload, keys=("volume_m5", "volume_m5_usd", "volume_5m"))
    market_cap = _first_positive_float(summary, payload, keys=("market_cap_usd", "market_cap", "fdv"))
    try:
        buys5m = int(summary.get("txns_m5_buys") or summary.get("buys_5m") or payload.get("txns_m5_buys") or payload.get("buys_5m") or 0)
    except Exception:
        buys5m = 0
    try:
        sells5m = int(summary.get("txns_m5_sells") or summary.get("sells_5m") or payload.get("txns_m5_sells") or payload.get("sells_5m") or 0)
    except Exception:
        sells5m = 0
    try:
        price_change_m5 = float(summary.get("price_change_m5") or summary.get("price_change_5m") or payload.get("price_change_m5") or payload.get("price_change_5m") or 0.0)
    except Exception:
        price_change_m5 = 0.0
    try:
        price_change_h1 = float(summary.get("price_change_h1") or summary.get("price_change_1h") or payload.get("price_change_h1") or payload.get("price_change_1h") or 0.0)
    except Exception:
        price_change_h1 = 0.0

    buy_sell_ratio = float(buys5m) / float(max(sells5m, 1)) if buys5m > 0 else 0.0
    sell_buy_ratio = float(sells5m) / float(max(buys5m, 1)) if buys5m > 0 else 0.0
    vol_liq_ratio = (float(vol5m) / float(liq)) if vol5m is not None and liq is not None and liq > 0.0 else 0.0
    return (
        age_min is not None
        and 24 * 60 < age_min <= 45 * 24 * 60
        and liq is not None
        and liq >= 25_000.0
        and vol5m is not None
        and vol5m >= 5_000.0
        and buys5m >= 25
        and buy_sell_ratio >= 1.8
        and sell_buy_ratio <= 0.85
        and vol_liq_ratio <= 1.2
        and (
            4.0 <= price_change_m5 <= 35.0
            or 18.0 <= price_change_h1 <= 160.0
        )
        and market_cap is not None
        and 50_000.0 <= market_cap <= 10_000_000.0
    )


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
    x_heavy_authors = int(metrics.get("x_heavy_author_count") or 0)
    x_verified_authors = int(metrics.get("x_verified_author_count") or 0)
    x_author_followers = int(metrics.get("x_author_followers") or 0)
    x_likes = int(metrics.get("x_likes") or 0)
    narrative_hits = metrics.get("narrative_hits") if isinstance(metrics.get("narrative_hits"), list) else []
    viral_theme_hits = metrics.get("viral_theme_hits") if isinstance(metrics.get("viral_theme_hits"), list) else []
    viral_x_signal = bool(metrics.get("viral_x_signal"))
    community_takeover = bool(metrics.get("community_takeover"))
    independent_flow_confirmed = bool(metrics.get("independent_flow_confirmed"))
    paid_visibility = bool(metrics.get("paid_visibility"))
    volume_window_phase = str(metrics.get("volume_window_phase") or "").strip().lower()
    try:
        volume_pace_ratio = float(metrics.get("volume_pace_ratio") or 0.0)
    except Exception:
        volume_pace_ratio = 0.0
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
    if (
        x_mentions >= policy.social_support_min_mentions
        and x_authors >= policy.social_support_min_authors
    ):
        confirmations.append("social_support")
    if x_heavy_authors > 0:
        confirmations.append("heavy_x_support")
    if x_verified_authors >= 2 or x_author_followers >= 50_000:
        confirmations.append("credible_x_reach")
    if narrative_hits:
        confirmations.append("narrative_alignment")
    if community_takeover:
        confirmations.append("community_takeover")
    if viral_theme_hits:
        confirmations.append("viral_theme")
    if (
        viral_theme_hits
        and x_mentions >= policy.viral_social_min_mentions
        and x_authors >= policy.viral_social_min_authors
        and (
            x_likes >= policy.viral_social_min_likes
            or x_heavy_authors > 0
            or x_verified_authors > 0
            or viral_x_signal
        )
    ):
        confirmations.append("viral_x_momentum")

    liq = 0.0
    buys5m = 0
    sells5m = 0
    vol5m = 0.0
    price_change_m5 = 0.0
    market_cap = 0.0
    if isinstance(dex_summary, dict):
        try:
            liq = float(dex_summary.get("liquidity_usd") or 0.0)
        except Exception:
            liq = 0.0
        try:
            buys5m = int(dex_summary.get("txns_m5_buys") or dex_summary.get("buys_5m") or 0)
        except Exception:
            buys5m = 0
        try:
            sells5m = int(dex_summary.get("txns_m5_sells") or dex_summary.get("sells_5m") or 0)
        except Exception:
            sells5m = 0
        try:
            vol5m = float(
                dex_summary.get("volume_m5")
                or dex_summary.get("volume_m5_usd")
                or dex_summary.get("volume_5m")
                or 0.0
            )
        except Exception:
            vol5m = 0.0
        try:
            price_change_m5 = float(
                dex_summary.get("price_change_m5")
                or dex_summary.get("price_change_5m")
                or 0.0
            )
        except Exception:
            price_change_m5 = 0.0
        try:
            market_cap = float(
                dex_summary.get("market_cap_usd")
                or dex_summary.get("market_cap")
                or dex_summary.get("fdv")
                or 0.0
            )
        except Exception:
            market_cap = 0.0
    if liq >= policy.min_market_support_liq_usd and buys5m >= policy.market_support_min_buys5m:
        confirmations.append("market_support")
    buy_sell_ratio = buys5m / max(1, sells5m)
    sell_buy_ratio = sells5m / max(1, buys5m)
    if (
        independent_flow_confirmed
        and (not paid_visibility or vol5m >= 10_000)
        and liq >= policy.min_market_support_liq_usd
        and vol5m >= 5_000
        and buys5m >= policy.market_support_min_buys5m
        and sell_buy_ratio <= 1.2
        and (volume_pace_ratio >= 1.0 or volume_window_phase in {"entering", "active", "surging"})
    ):
        confirmations.append("dex_flow_confirmed")
    if (
        buys5m >= max(policy.entry_confirm_buys5m_min * 2, 30)
        and sell_buy_ratio <= 1.2
        and price_change_m5 >= -5.0
        and vol5m >= 7_000
        and liq >= policy.min_market_support_liq_usd
    ):
        confirmations.append("dex_buyer_pressure")
    if (
        liq >= max(policy.entry_chase_min_liq_usd, policy.min_market_support_liq_usd)
        and vol5m >= 10_000
        and buys5m >= max(policy.entry_confirm_buys5m_min, policy.market_support_min_buys5m)
        and price_change_m5 >= 5.0
        and 50_000 <= market_cap <= 5_000_000
    ):
        confirmations.append("dex_momentum")
    if (
        buys5m >= policy.entry_confirm_buys5m_min
        and buy_sell_ratio >= 1.35
        and price_change_m5 >= 0.0
        and vol5m >= 5_000
    ):
        confirmations.append("entry_buy_pressure")
    high_conviction_dex_breadth_proxy = (
        buyers_5m <= 0
        and buys5m >= max(policy.entry_confirm_buys5m_min * 12, 180)
        and buy_sell_ratio >= 20.0
        and sell_buy_ratio <= 0.08
        and 0.0 <= price_change_m5 <= 18.0
        and vol5m >= 5_000
        and liq >= max(policy.entry_chase_min_liq_usd * 4, 100_000.0)
        and (vol5m / max(liq, 1.0)) <= 0.20
        and 50_000 <= market_cap <= 5_000_000
    )
    thin_ignition_breadth_proxy = (
        buyers_5m <= 0
        and buys5m >= max(policy.entry_confirm_buys5m_min * 10, 150)
        and buy_sell_ratio >= 3.0
        and sell_buy_ratio <= 0.35
        and 2.0 <= price_change_m5 <= 18.0
        and 2_500 <= vol5m < 5_000
        and liq >= max(policy.entry_chase_min_liq_usd * 2, 50_000.0)
        and (vol5m / max(liq, 1.0)) <= 0.20
        and 50_000 <= market_cap <= 7_500_000
    )
    if (
        buyers_5m <= 0
        and buys5m >= max(policy.entry_confirm_buys5m_min * 6, 90)
        and buy_sell_ratio >= 2.2
        and sell_buy_ratio <= 0.50
        and 5.0 <= price_change_m5 <= 35.0
        and vol5m >= 10_000
        and liq >= max(policy.entry_chase_min_liq_usd, 25_000.0)
        and (vol5m / max(liq, 1.0)) <= 3.0
    ) or high_conviction_dex_breadth_proxy or thin_ignition_breadth_proxy:
        confirmations.append("winner_breadth_proxy")
        if thin_ignition_breadth_proxy:
            confirmations.append("thin_ignition_watch")
    if dex_accumulation_watch_signal(metrics, dex_summary):
        confirmations.append("dex_accumulation_watch")
    if viral_theme_dex_momentum_signal(metrics, dex_summary):
        confirmations.append("viral_dex_momentum")
    if dormant_revival_watch_signal(metrics, dex_summary):
        confirmations.append("dormant_revival_watch")

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


def entry_quality_profile(
    *,
    metrics: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    policy = candidate_signal_policy()
    payload = metrics if isinstance(metrics, dict) else {}
    summary = dex_summary if isinstance(dex_summary, dict) else {}

    price_change_m5 = _first_positive_float(summary, payload, keys=("price_change_m5", "price_change_5m"))
    price_change_h1 = _first_positive_float(summary, payload, keys=("price_change_h1", "price_change_1h"))
    liq = _first_positive_float(summary, payload, keys=("liquidity_usd", "liquidity"))
    vol5m = _first_positive_float(summary, payload, keys=("volume_m5", "volume_m5_usd", "volume_5m"))
    buys5m = int(summary.get("txns_m5_buys") or summary.get("buys_5m") or payload.get("txns_m5_buys") or payload.get("buys_5m") or 0)
    sells5m = int(summary.get("txns_m5_sells") or summary.get("sells_5m") or payload.get("txns_m5_sells") or payload.get("sells_5m") or 0)
    buyers_5m = int(payload.get("unique_buyers_5m") or 0)
    tracked_hits = int(payload.get("tracked_wallet_hits") or 0)
    kol_hits = int(payload.get("kol_wallet_hits") or 0)
    trusted_wallet_support = tracked_hits > 0
    any_wallet_support = trusted_wallet_support or kol_hits > 0
    buy_sell_ratio = (float(buys5m) / float(max(sells5m, 1))) if buys5m > 0 else 0.0
    dex_breadth_proxy = (
        buyers_5m <= 0
        and buys5m >= max(policy.entry_confirm_buys5m_min * 3, 40)
        and buy_sell_ratio >= policy.entry_chase_max_buy_sell_ratio
        and price_change_m5 is not None
        and price_change_m5 >= 0.0
        and vol5m is not None
        and vol5m >= 5_000
        and liq is not None
        and liq >= policy.entry_chase_min_liq_usd
    )
    volume_liquidity_ratio = (
        float(vol5m) / float(liq)
        if vol5m is not None and liq is not None and liq > 0.0
        else None
    )

    reasons: list[str] = []
    supports: list[str] = []
    score = 50
    is_extended = (
        (price_change_m5 is not None and price_change_m5 >= policy.entry_chase_price_change_5m)
        or (price_change_h1 is not None and price_change_h1 >= policy.entry_chase_price_change_h1)
    )
    if buyers_5m >= policy.entry_confirm_breadth_min:
        supports.append("entry_buyer_breadth")
        score += 12
    elif dex_breadth_proxy:
        supports.append("entry_dex_breadth_proxy")
        score += 8
    if buys5m >= policy.entry_confirm_buys5m_min:
        supports.append("entry_buy_pressure")
        score += 10
    if buy_sell_ratio >= policy.entry_chase_max_buy_sell_ratio:
        supports.append("entry_buy_sell_imbalance")
        score += 8
    if liq is not None and liq >= policy.entry_chase_min_liq_usd:
        supports.append("entry_liquidity_floor")
        score += 8
    if any_wallet_support:
        supports.append("entry_wallet_support")
        score += 10

    if is_extended:
        reasons.append("entry_extended")
        score -= 12
        if liq is None or liq < policy.entry_chase_min_liq_usd:
            reasons.append("entry_extended_thin_liquidity")
            score -= 14
        if buyers_5m < policy.entry_confirm_breadth_min and not dex_breadth_proxy and not any_wallet_support:
            reasons.append("entry_extended_without_breadth")
            score -= 14
        if buy_sell_ratio < policy.entry_chase_max_buy_sell_ratio and not trusted_wallet_support:
            reasons.append("entry_extended_buy_pressure_missing")
            score -= 10
    if volume_liquidity_ratio is not None and volume_liquidity_ratio > 6.0 and not any_wallet_support:
        reasons.append("entry_hype_volume_liquidity")
        score -= 10

    score = max(0, min(100, score))
    if any(reason.startswith("entry_extended_") for reason in reasons):
        tier = "chase_risk"
    elif score >= 78:
        tier = "confirmed_entry"
    elif score >= 58:
        tier = "developing_entry"
    else:
        tier = "thin_entry"

    return {
        "tier": tier,
        "score": score,
        "reasons": reasons,
        "supports": supports,
        "metrics": {
            "price_change_m5": price_change_m5,
            "price_change_h1": price_change_h1,
            "liquidity_usd": liq,
            "volume_m5": vol5m,
            "volume_liquidity_ratio": round(volume_liquidity_ratio, 3) if volume_liquidity_ratio is not None else None,
            "txns_m5_buys": buys5m,
            "txns_m5_sells": sells5m,
            "buy_sell_ratio": round(buy_sell_ratio, 3),
            "unique_buyers_5m": buyers_5m,
            "dex_breadth_proxy": dex_breadth_proxy,
            "tracked_wallet_hits": tracked_hits,
            "kol_wallet_hits": kol_hits,
        },
        "policy": {
            "entry_chase_price_change_5m": policy.entry_chase_price_change_5m,
            "entry_chase_price_change_h1": policy.entry_chase_price_change_h1,
            "entry_chase_max_buy_sell_ratio": policy.entry_chase_max_buy_sell_ratio,
            "entry_chase_min_liq_usd": policy.entry_chase_min_liq_usd,
            "entry_confirm_breadth_min": policy.entry_confirm_breadth_min,
            "entry_confirm_buys5m_min": policy.entry_confirm_buys5m_min,
        },
    }


def adversarial_signal_flags(
    *,
    metrics: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
    anti_wash_top_wallet_share: float,
    anti_wash_unique_wallets_30s: int,
    min_unique_buyers_5m: int,
    min_burst_count_60s: int,
    max_sell_ratio_5m: float,
    max_vol_liq_ratio_5m: float,
    shallow_liq_usd: float,
    max_single_holder_ratio: float | None = None,
    min_volume_market_cap_ratio: float | None = None,
    social_min_author_ratio: float | None = None,
    social_min_mentions: int | None = None,
) -> list[str]:
    payload = metrics if isinstance(metrics, dict) else {}
    flags: list[str] = []
    tracked_hits = int(payload.get("tracked_wallet_hits") or 0)
    kol_hits = int(payload.get("kol_wallet_hits") or 0)
    buyers_5m = int(payload.get("unique_buyers_5m") or 0)
    burst_60s = int(payload.get("burst_count_60s") or 0)
    top_wallet_share = float(payload.get("top_wallet_share_30s") or 0.0)
    unique_wallets_30s = int(payload.get("unique_wallets_30s") or 0)
    trusted_wallet_support = tracked_hits > 0
    any_wallet_support = tracked_hits > 0 or kol_hits > 0

    if (
        burst_60s >= min_burst_count_60s
        and buyers_5m < min_unique_buyers_5m
        and not any_wallet_support
    ):
        flags.append("burst_without_breadth")
    if (
        top_wallet_share >= anti_wash_top_wallet_share
        and unique_wallets_30s <= max(anti_wash_unique_wallets_30s + 1, 3)
        and not any_wallet_support
    ):
        flags.append("concentrated_wallet_flow")

    summary = dex_summary if isinstance(dex_summary, dict) else {}
    liq = float(summary.get("liquidity_usd") or 0.0)
    vol5m = float(summary.get("volume_m5") or summary.get("volume_m5_usd") or summary.get("volume_5m") or 0.0)
    buys5m = int(summary.get("txns_m5_buys") or summary.get("buys_5m") or 0)
    sells5m = int(summary.get("txns_m5_sells") or summary.get("sells_5m") or 0)
    dex_flow_confirmed = (
        liq >= shallow_liq_usd
        and vol5m >= 5_000.0
        and buys5m >= max(8, min_burst_count_60s)
    )

    if liq > 0.0:
        if liq < shallow_liq_usd and vol5m >= liq and not any_wallet_support:
            flags.append("shallow_liquidity_hype")
        if (vol5m / liq) > max_vol_liq_ratio_5m and not any_wallet_support:
            flags.append("volume_liquidity_imbalance")
    if buys5m > 0:
        sell_ratio = float(sells5m) / float(buys5m)
        if sell_ratio > max_sell_ratio_5m and not any_wallet_support:
            flags.append("sell_pressure_elevated")
    else:
        sell_ratio = 0.0

    price_change_m5 = _first_positive_float(
        summary,
        payload,
        keys=("price_change_m5", "price_change_5m"),
    )
    if price_change_m5 is None:
        try:
            price_change_m5 = float(summary.get("price_change_m5") or summary.get("price_change_5m") or 0.0)
        except Exception:
            price_change_m5 = 0.0
    try:
        price_change_h1 = float(summary.get("price_change_h1") or summary.get("price_change_1h") or payload.get("price_change_h1") or payload.get("price_change_1h") or 0.0)
    except Exception:
        price_change_h1 = 0.0
    repeat_count = int(payload.get("dex_scan_repeat_count") or 0)
    try:
        volume_delta = float(payload.get("dex_scan_volume_delta_5m") or 0.0)
    except Exception:
        volume_delta = 0.0
    sources = payload.get("discovery_sources") if isinstance(payload.get("discovery_sources"), list) else []
    credible_source = bool(payload.get("community_takeover")) or "community_takeover" in sources
    x_mentions = int(payload.get("x_tweet_count") or 0)
    x_authors = int(payload.get("x_unique_authors") or 0)
    x_heavy_authors = int(payload.get("x_heavy_author_count") or 0)
    x_verified_authors = int(payload.get("x_verified_author_count") or 0)
    x_author_followers = int(payload.get("x_author_followers") or 0)
    credible_social_support = x_heavy_authors > 0 or x_verified_authors >= 2 or x_author_followers >= 50_000
    viral_x_support = bool(payload.get("viral_x_signal")) or (
        payload.get("viral_theme_hits")
        and x_mentions >= 8
        and x_authors >= 4
        and (x_author_followers >= 25_000 or x_verified_authors > 0 or x_heavy_authors > 0)
    )
    credible_social_support = credible_social_support or viral_x_support
    synthetic_churn_shape = (
        buys5m >= 25
        and sells5m >= 20
        and 0.65 <= sell_ratio <= 1.35
        and abs(float(price_change_m5 or 0.0)) <= 3.0
        and buyers_5m < min_unique_buyers_5m
        and burst_60s < min_burst_count_60s
        and repeat_count >= 3
        and volume_delta < 500.0
        and not any_wallet_support
        and not credible_source
        and not credible_social_support
    )
    if synthetic_churn_shape:
        flags.append("synthetic_churn_without_independent_flow")
    buy_sell_ratio = float(buys5m) / float(max(sells5m, 1)) if buys5m > 0 else 0.0
    sell_buy_ratio = float(sells5m) / float(max(buys5m, 1)) if buys5m > 0 else 0.0
    has_current_flow_support = (
        any_wallet_support
        or credible_source
        or credible_social_support
        or buyers_5m >= min_unique_buyers_5m
        or (
            buys5m >= max(25, min_burst_count_60s * 3)
            and buy_sell_ratio >= 1.8
            and sell_buy_ratio <= 0.85
        )
    )
    if (
        (float(price_change_m5 or 0.0) >= 35.0 or price_change_h1 >= 140.0)
        and not has_current_flow_support
    ):
        flags.append("price_pump_without_flow")
    if (
        liq > 0.0
        and (vol5m / liq) >= max(1.5, max_vol_liq_ratio_5m * 0.75)
        and buyers_5m < min_unique_buyers_5m
        and not has_current_flow_support
    ):
        flags.append("liquidity_volume_spike")
    if (
        float(price_change_m5 or 0.0) >= 20.0
        and buys5m < max(12, min_burst_count_60s * 2)
        and not any_wallet_support
        and not credible_social_support
    ):
        flags.append("one_sided_chart_risk")

    holder_ratio = _first_positive_float(
        payload,
        summary,
        keys=("top_holder_ratio", "top_holder_pct", "wallet_top_holder_pct"),
    )
    if (
        holder_ratio is not None
        and max_single_holder_ratio is not None
        and holder_ratio > max_single_holder_ratio
        and not trusted_wallet_support
    ):
        flags.append("single_holder_supply_control")

    market_cap = _first_positive_float(
        summary,
        payload,
        keys=("market_cap_usd", "market_cap", "fdv"),
    )
    volume = _first_positive_float(
        summary,
        payload,
        keys=("volume_h24", "volume_24h", "volume_h24_usd", "volume_24h_usd", "volume_usd"),
    )
    if volume is None:
        volume = _first_positive_float(summary, payload, keys=("volume_h1", "volume_h1_usd"))
    if (
        market_cap is not None
        and volume is not None
        and min_volume_market_cap_ratio is not None
        and (volume / market_cap) < min_volume_market_cap_ratio
        and not trusted_wallet_support
    ):
        flags.append("low_volume_market_cap_imbalance")

    boosts = int(payload.get("dexscreener_boosts_count") or payload.get("dex_boosts") or 0)
    paid_visibility = bool(payload.get("paid_visibility"))
    if (
        (boosts > 0 or paid_visibility)
        and buyers_5m < min_unique_buyers_5m
        and burst_60s < min_burst_count_60s
        and not dex_flow_confirmed
        and not trusted_wallet_support
    ):
        flags.append("paid_visibility_without_flow")

    min_social_mentions = int(social_min_mentions or 0)
    if (
        x_mentions >= min_social_mentions > 0
        and social_min_author_ratio is not None
        and (float(x_authors) / float(x_mentions)) < social_min_author_ratio
        and not trusted_wallet_support
        and not credible_social_support
    ):
        flags.append("social_echo_chamber")

    entry_profile = entry_quality_profile(metrics=payload, dex_summary=summary)
    if str(entry_profile.get("tier") or "") == "chase_risk" and not trusted_wallet_support:
        flags.extend(str(item) for item in entry_profile.get("reasons") or [])

    deduped: list[str] = []
    for item in flags:
        if item not in deduped:
            deduped.append(item)
    return deduped


def winner_send_guard_reasons(
    *,
    metrics: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
    confirmations: list[str],
) -> list[str]:
    policy = candidate_signal_policy()
    payload = metrics if isinstance(metrics, dict) else {}
    summary = dex_summary if isinstance(dex_summary, dict) else {}
    confirmation_set = set(confirmations)
    if "market_support" not in confirmation_set:
        return []

    buyers_5m = int(payload.get("unique_buyers_5m") or 0)
    burst_60s = int(payload.get("burst_count_60s") or 0)
    unique_wallets_30s = int(payload.get("unique_wallets_30s") or 0)
    trusted_support = bool(
        {
            "tracked_wallet_flow",
            "kol_wallet_flow",
            "heavy_x_support",
            "credible_x_reach",
            "social_support",
            "community_takeover",
            "viral_x_momentum",
        }
        & confirmation_set
    )
    try:
        buys5m = int(summary.get("txns_m5_buys") or summary.get("buys_5m") or 0)
    except Exception:
        buys5m = 0
    try:
        sells5m = int(summary.get("txns_m5_sells") or summary.get("sells_5m") or 0)
    except Exception:
        sells5m = 0
    try:
        price_change_m5 = float(summary.get("price_change_m5") or summary.get("price_change_5m") or 0.0)
    except Exception:
        price_change_m5 = 0.0
    buy_sell_ratio = float(buys5m) / float(max(sells5m, 1)) if buys5m > 0 else 0.0
    sell_buy_ratio = float(sells5m) / float(max(buys5m, 1)) if buys5m > 0 else 0.0

    has_real_breadth = buyers_5m >= policy.min_unique_buyers_5m
    has_developing_breadth = buyers_5m >= 1 and (
        (
            unique_wallets_30s >= 4
            and burst_60s >= max(3, policy.min_burst_count_60s // 2)
        )
        or (
            5.0 <= price_change_m5 <= 35.0
            and buy_sell_ratio >= policy.entry_chase_max_buy_sell_ratio
            and sell_buy_ratio <= 0.75
        )
    )
    has_high_quality_proxy = bool({"winner_breadth_proxy", "dormant_revival_watch"} & confirmation_set)

    reasons: list[str] = []
    if not (trusted_support or has_real_breadth or has_developing_breadth or has_high_quality_proxy):
        reasons.append("winner_breadth_missing")
    if price_change_m5 < 0.0 and not trusted_support:
        reasons.append("winner_entry_fading")
    if sell_buy_ratio > 1.0 and not trusted_support:
        reasons.append("winner_sell_pressure")
    if buyers_5m <= 0 and buy_sell_ratio < 2.0 and not trusted_support:
        reasons.append("winner_hype_churn")
    return reasons


def candidate_send_reasons(
    *,
    attention_score: float | None,
    creator_score: float,
    extra: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
) -> tuple[bool, list[str], list[str]]:
    policy = candidate_signal_policy()
    payload = extra if isinstance(extra, dict) else {}
    route = payload.get("route_decision") if isinstance(payload.get("route_decision"), dict) else {}
    route_tier = str(route.get("tier") or "").strip().lower()
    route_confidence = float(route.get("route_confidence") or 0.0)
    attn = float(attention_score or 0.0)
    reasons, confirmations = candidate_confirmation_signals(
        attention_score=attn,
        extra=extra,
        dex_summary=dex_summary,
    )
    adversarial_flags = adversarial_signal_flags(
        metrics=(payload.get("attention_metrics") if isinstance(payload.get("attention_metrics"), dict) else {}),
        dex_summary=dex_summary,
        anti_wash_top_wallet_share=policy.anti_wash_top_wallet_share,
        anti_wash_unique_wallets_30s=policy.anti_wash_unique_wallets_30s,
        min_unique_buyers_5m=policy.min_unique_buyers_5m,
        min_burst_count_60s=policy.min_burst_count_60s,
        max_sell_ratio_5m=policy.adversarial_max_sell_ratio_5m,
        max_vol_liq_ratio_5m=policy.adversarial_max_vol_liq_ratio_5m,
        shallow_liq_usd=policy.adversarial_shallow_liq_usd,
        max_single_holder_ratio=policy.adversarial_max_single_holder_ratio,
        min_volume_market_cap_ratio=policy.adversarial_min_volume_market_cap_ratio,
        social_min_author_ratio=policy.adversarial_social_min_author_ratio,
        social_min_mentions=policy.adversarial_social_min_mentions,
    )
    confirmation_set = set(confirmations)
    if payload.get("wallet_guard_watch_only"):
        reasons.append("wallet_guard_watch_only")
    hard_quality_confirmed = bool({"tracked_wallet_flow", "market_support", "heavy_x_support"} & confirmation_set)
    soft_quality_confirmed = bool(
        {
            "kol_wallet_flow",
            "social_support",
            "credible_x_reach",
            "narrative_alignment",
            "community_takeover",
            "viral_x_momentum",
        }
        & confirmation_set
    )
    flow_strength_confirmed = {"buyer_breadth", "burst_strength"}.issubset(confirmation_set)
    route_fast_lane = route_tier == "sniper" or (route_tier == "heating_up" and route_confidence >= 0.75)

    has_creator_support = creator_score >= policy.strong_creator_threshold and attn >= policy.creator_attention_floor
    has_attention_only = attn >= policy.strong_attention_threshold
    has_balanced_quality = creator_score >= policy.creator_attention_target and attn >= policy.creator_attention_target
    has_dex_momentum_breakout = {
        "market_support",
        "dex_momentum",
        "entry_buy_pressure",
    }.issubset(confirmation_set)
    has_dex_pressure_breakout = {
        "market_support",
        "dex_buyer_pressure",
        "entry_buy_pressure",
    }.issubset(confirmation_set)
    has_dex_breakout = has_dex_momentum_breakout or has_dex_pressure_breakout
    has_dex_accumulation_breakout = {
        "market_support",
        "dex_accumulation_watch",
    }.issubset(confirmation_set)
    has_dormant_revival_breakout = (
        "dormant_revival_watch" in confirmation_set
        and bool({"dex_buyer_pressure", "entry_buy_pressure", "dex_flow_confirmed"} & confirmation_set)
    )
    has_viral_breakout = (
        (
            {"market_support", "viral_x_momentum"}.issubset(confirmation_set)
            or {"market_support", "viral_dex_momentum"}.issubset(confirmation_set)
        )
        and bool({"entry_buy_pressure", "dex_flow_confirmed", "dex_buyer_pressure"} & confirmation_set)
    )
    winner_guard_reasons = winner_send_guard_reasons(
        metrics=(payload.get("attention_metrics") if isinstance(payload.get("attention_metrics"), dict) else {}),
        dex_summary=dex_summary,
        confirmations=confirmations,
    )

    if (
        len(confirmations) < policy.min_send_confirmation_signals
        and not has_dex_accumulation_breakout
        and not has_dormant_revival_breakout
        and not has_viral_breakout
        and not (
            attn >= policy.exceptional_attention_threshold
            and flow_strength_confirmed
            and (hard_quality_confirmed or route_fast_lane)
        )
    ):
        reasons.append(f"send_confirmation_signals<{policy.min_send_confirmation_signals}")
    if not (
        hard_quality_confirmed
        or (soft_quality_confirmed and flow_strength_confirmed)
        or route_fast_lane
    ):
        reasons.append("quality_confirmation_missing")
    severe_adversarial_flags = {
        "single_holder_supply_control",
        "low_volume_market_cap_imbalance",
        "paid_visibility_without_flow",
        "social_echo_chamber",
        "synthetic_churn_without_independent_flow",
        "entry_extended_thin_liquidity",
        "entry_extended_without_breadth",
        "entry_extended_buy_pressure_missing",
        "entry_hype_volume_liquidity",
        "price_pump_without_flow",
        "liquidity_volume_spike",
        "one_sided_chart_risk",
    }
    has_severe_adversarial_flag = bool(severe_adversarial_flags & set(adversarial_flags))
    if adversarial_flags and not route_fast_lane and (not hard_quality_confirmed or has_severe_adversarial_flag):
        reasons.extend(item for item in adversarial_flags if item not in reasons)
    reasons.extend(item for item in winner_guard_reasons if item not in reasons)

    eligible = (
        has_attention_only
        or has_creator_support
        or has_balanced_quality
        or has_dex_breakout
        or has_dex_accumulation_breakout
        or has_dormant_revival_breakout
        or has_viral_breakout
    ) and not reasons
    if not (
        has_attention_only
        or has_creator_support
        or has_balanced_quality
        or has_dex_breakout
        or has_dex_accumulation_breakout
        or has_dormant_revival_breakout
        or has_viral_breakout
    ):
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
    if strong_signals >= policy.strong_signal_discount_min_count:
        confirm_target = max(1, confirm_target - policy.strong_signal_confirmation_discount)
    elif strong_signals <= policy.low_quality_extra_min_count:
        confirm_target = confirm_target + policy.low_quality_extra_confirmations

    if isinstance(dex_summary, dict):
        buys5m = int(dex_summary.get("txns_m5_buys") or 0)
        sells5m = int(dex_summary.get("txns_m5_sells") or 0)
        sell_ratio = (float(sells5m) / float(buys5m)) if buys5m > 0 else 0.0
        if sell_ratio > policy.max_sell_ratio5m:
            confirm_target += 1
            reasons.append("sell_pressure_high")
        adversarial_flags = adversarial_signal_flags(
            metrics=metrics,
            dex_summary=dex_summary,
            anti_wash_top_wallet_share=candidate_signal_policy().anti_wash_top_wallet_share,
            anti_wash_unique_wallets_30s=candidate_signal_policy().anti_wash_unique_wallets_30s,
            min_unique_buyers_5m=candidate_signal_policy().min_unique_buyers_5m,
            min_burst_count_60s=candidate_signal_policy().min_burst_count_60s,
            max_sell_ratio_5m=policy.max_sell_ratio5m,
            max_vol_liq_ratio_5m=route_signal_policy().adversarial_max_vol_liq_ratio_5m,
            shallow_liq_usd=route_signal_policy().adversarial_shallow_liq_usd,
            max_single_holder_ratio=candidate_signal_policy().adversarial_max_single_holder_ratio,
            min_volume_market_cap_ratio=candidate_signal_policy().adversarial_min_volume_market_cap_ratio,
            social_min_author_ratio=candidate_signal_policy().adversarial_social_min_author_ratio,
            social_min_mentions=candidate_signal_policy().adversarial_social_min_mentions,
        )
        severe_adversarial_flags = {
            "single_holder_supply_control",
            "low_volume_market_cap_imbalance",
            "paid_visibility_without_flow",
            "social_echo_chamber",
            "entry_extended_thin_liquidity",
            "entry_extended_without_breadth",
            "entry_extended_buy_pressure_missing",
            "entry_hype_volume_liquidity",
        }
        has_severe_adversarial_flag = bool(severe_adversarial_flags & set(adversarial_flags))
        if adversarial_flags and (tracked_hits <= 0 or has_severe_adversarial_flag):
            confirm_target += 1
            reasons.extend(item for item in adversarial_flags if item not in reasons)
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
    if buyers_5m >= policy.route_buyer_breadth_min:
        confirmations.append("buyer_breadth")
    if burst_60s >= policy.route_burst_strength_min:
        confirmations.append("burst_strength")
    if liq >= policy.heating_min_liq_usd and buys5m >= policy.route_market_support_min_buys5m:
        confirmations.append("market_support")
    if attention >= policy.heating_min_attention:
        confirmations.append("attention_support")
    if x_mentions >= policy.heating_min_x_mentions and x_authors >= policy.heating_min_x_authors:
        confirmations.append("social_support")

    adversarial_flags = adversarial_signal_flags(
        metrics=metrics,
        dex_summary=dex_summary,
        anti_wash_top_wallet_share=candidate_signal_policy().anti_wash_top_wallet_share,
        anti_wash_unique_wallets_30s=candidate_signal_policy().anti_wash_unique_wallets_30s,
        min_unique_buyers_5m=candidate_signal_policy().min_unique_buyers_5m,
        min_burst_count_60s=candidate_signal_policy().min_burst_count_60s,
        max_sell_ratio_5m=policy.adversarial_max_sell_ratio_5m,
        max_vol_liq_ratio_5m=policy.adversarial_max_vol_liq_ratio_5m,
        shallow_liq_usd=policy.adversarial_shallow_liq_usd,
        max_single_holder_ratio=candidate_signal_policy().adversarial_max_single_holder_ratio,
        min_volume_market_cap_ratio=candidate_signal_policy().adversarial_min_volume_market_cap_ratio,
        social_min_author_ratio=candidate_signal_policy().adversarial_social_min_author_ratio,
        social_min_mentions=candidate_signal_policy().adversarial_social_min_mentions,
    )
    entry_profile = entry_quality_profile(metrics=metrics, dex_summary=dex_summary)

    flow_confirmations = [item for item in confirmations if item in {"buyer_breadth", "burst_strength"}]
    flow_strength_confirmed = len(flow_confirmations) >= 2
    quality_confirmations = [
        item
        for item in confirmations
        if item in {"tracked_wallet_flow", "kol_wallet_flow", "market_support", "social_support"}
    ]
    hard_quality_confirmations = [
        item
        for item in confirmations
        if item in {"tracked_wallet_flow", "market_support"}
    ]
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
            - (0.05 if not hard_quality_confirmations else 0.0)
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
    for flag in adversarial_flags:
        if flag not in sniper_blockers and not hard_quality_confirmations:
            sniper_blockers.append(flag)
        if flag not in blockers and (
            not hard_quality_confirmations
            or len(adversarial_flags) >= policy.adversarial_flags_block_heating
        ):
            blockers.append(flag)

    if (
        not blockers
        and sniper_ready
    ):
        route_tier = "sniper"
    elif (
        not blockers
        and len(confirmations) >= policy.heating_min_confirmations
        and (
            attention >= policy.heating_min_attention
            or elite_score >= max(7, policy.sniper_min_elite - 1)
            or tracked_hits > 0
            or kol_hits > 0
        )
        and (
            bool(hard_quality_confirmations)
            or (
                flow_strength_confirmed
                and attention >= policy.sniper_min_attention
                and elite_score >= max(7, policy.sniper_min_elite - 1)
            )
        )
        and (
            len(adversarial_flags) < policy.adversarial_flags_block_heating
            or bool(hard_quality_confirmations)
        )
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
        and hard_quality_confirmations
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
        "entry_quality": entry_profile,
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
    policy = route_signal_policy()
    payload = extra if isinstance(extra, dict) else {}
    route = payload.get("route_decision") if isinstance(payload.get("route_decision"), dict) else {}
    tier = str(route.get("tier") or "")
    confirmations = route.get("confirmations") if isinstance(route.get("confirmations"), list) else []
    blockers = route.get("blockers") if isinstance(route.get("blockers"), list) else []
    route_confidence = float(route.get("route_confidence") or 0.0)
    if payload.get("wallet_guard_watch_only"):
        return False, ["wallet_guard_watch_only", *blockers]
    confirmation_count = len(confirmations)
    has_market_support = "market_support" in confirmations
    has_smart_wallet_support = bool({"tracked_wallet_flow", "kol_wallet_flow"} & set(confirmations))
    has_flow_support = bool({"buyer_breadth", "burst_strength"} & set(confirmations))
    if tier == "sniper":
        return True, ["sniper_route", f"route_confidence:{route_confidence:.2f}", *confirmations]
    if tier != "heating_up":
        return False, blockers or ["route_not_heating_ready"]
    if (
        tier == "heating_up"
        and confirmation_count >= policy.heating_delivery_min_confirmations
        and has_flow_support
        and (has_market_support or has_smart_wallet_support)
        and route_confidence >= policy.heating_delivery_min_confidence
    ):
        return True, [f"route_confidence:{route_confidence:.2f}", *confirmations]
    delivery_blockers = list(blockers)
    if route_confidence < policy.heating_delivery_min_confidence:
        delivery_blockers.append(f"route_confidence<{policy.heating_delivery_min_confidence:.2f}")
    if confirmation_count < policy.heating_delivery_min_confirmations:
        delivery_blockers.append(f"delivery_confirmations<{policy.heating_delivery_min_confirmations}")
    if not has_flow_support:
        delivery_blockers.append("delivery_flow_confirmation_missing")
    if not (has_market_support or has_smart_wallet_support):
        delivery_blockers.append("delivery_quality_confirmation_missing")
    return False, delivery_blockers or ["route_not_heating_ready"]

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
    social_support_min_mentions: int
    social_support_min_authors: int
    market_support_min_buys5m: int
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
    if (
        x_mentions >= policy.social_support_min_mentions
        and x_authors >= policy.social_support_min_authors
    ):
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
    if liq >= policy.min_market_support_liq_usd and buys5m >= policy.market_support_min_buys5m:
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
    policy = route_signal_policy()
    payload = extra if isinstance(extra, dict) else {}
    route = payload.get("route_decision") if isinstance(payload.get("route_decision"), dict) else {}
    tier = str(route.get("tier") or "")
    confirmations = route.get("confirmations") if isinstance(route.get("confirmations"), list) else []
    blockers = route.get("blockers") if isinstance(route.get("blockers"), list) else []
    route_confidence = float(route.get("route_confidence") or 0.0)
    if tier == "sniper":
        return True, ["sniper_route", f"route_confidence:{route_confidence:.2f}", *confirmations]
    if (
        tier == "heating_up"
        and confirmations
        and route_confidence >= policy.heating_delivery_min_confidence
    ):
        return True, [f"route_confidence:{route_confidence:.2f}", *confirmations]
    return False, blockers or ["route_not_heating_ready"]

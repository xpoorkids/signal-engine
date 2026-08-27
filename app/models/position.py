from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RISK_PROFILE_AGGRESSIVE = "aggressive"
EXIT_STYLE_CATALYST_RUNNER = "catalyst_runner"
EXECUTION_MODE_MANUAL = "manual"

POSITION_OPEN = "open"
POSITION_CLOSED = "closed"

CATALYST_STATES = {
    "rumor",
    "unverified",
    "verified",
    "active",
    "flow_confirmed",
    "high_conviction",
    "priced_in",
    "weakening",
    "invalidated",
    "expired",
    "false_or_retracted",
}

PRE_ENTRY_ACTIONS = {
    "BUY NOW",
    "BUY SMALL",
    "CATALYST BUY NOW",
    "CATALYST BUY SMALL",
    "WAIT",
    "WAIT FOR PULLBACK",
    "DO NOT CHASE",
    "AVOID",
    "HARD FAIL",
}

POSITION_ACTIONS = {
    "HOLD",
    "ADD SMALL ON CONFIRMATION",
    "TAKE PROFIT",
    "RECOVER PRINCIPAL",
    "TRIM",
    "HOLD RUNNER",
    "HOLD MOON BAG",
    "CATALYST WEAKENING",
    "CATALYST INVALIDATED",
    "SELL NOW",
    "EMERGENCY EXIT",
}


@dataclass(frozen=True)
class ManualFill:
    fill_id: str
    position_id: str
    side: str
    token_quantity: float
    gross_usd: float
    gross_sol: float
    net_amount_usd: float
    execution_price_usd: float
    fees_usd: float
    slippage_pct: float | None = None
    price_impact_pct: float | None = None
    tx_signature: str | None = None
    fill_ts: int | None = None
    source: str = "manual"
    notes: str | None = None


@dataclass(frozen=True)
class ManualPosition:
    position_id: str
    token: str
    symbol: str | None
    status: str
    risk_profile: str
    exit_style: str
    catalyst_mode: bool
    original_token_quantity: float
    current_token_quantity: float
    total_cash_invested_usd: float
    total_sol_invested: float
    total_fees_usd: float
    average_entry_price_usd: float
    first_entry_ts: int | None
    most_recent_entry_ts: int | None
    realized_proceeds_usd: float
    realized_profit_usd: float
    remaining_unrecovered_principal_usd: float
    current_executable_position_value_usd: float | None = None
    current_executable_return_pct: float | None = None
    highest_executable_position_value_usd: float = 0.0
    peak_executable_return_pct: float = 0.0
    drawdown_from_executable_peak_pct: float = 0.0
    original_thesis: str | None = None
    invalidation_conditions: list[str] = field(default_factory=list)
    catalyst_id: str | None = None
    created_ts: int | None = None
    updated_ts: int | None = None
    closed_ts: int | None = None


@dataclass(frozen=True)
class Catalyst:
    catalyst_id: str
    token: str
    catalyst_type: str
    title: str
    description: str | None
    original_source: str | None
    secondary_confirmations: list[str]
    first_observed_ts: int | None
    expected_start_ts: int | None
    expected_end_ts: int | None
    verification_status: str
    market_reaction_start_price_usd: float | None = None
    market_reaction_current_price_usd: float | None = None
    price_change_since_catalyst_pct: float | None = None
    unique_buyers_added_since_catalyst: int | None = None
    holders_added_since_catalyst: int | None = None
    net_sol_flow_since_catalyst: float | None = None
    liquidity_change_since_catalyst_pct: float | None = None
    creator_insider_sell_activity: str | None = None
    catalyst_confidence_pct: float = 0.0
    catalyst_flow_confirmation: bool = False
    catalyst_invalidation_reason: str | None = None
    created_ts: int | None = None
    updated_ts: int | None = None


@dataclass(frozen=True)
class ActionRecommendation:
    recommendation_id: str
    token: str
    position_id: str | None
    action: str
    action_mode: str
    risk_profile: str
    exit_style: str
    intended_size_usd: float | None
    current_position_value_usd: float | None
    current_executable_return_pct: float | None
    realized_return_usd: float
    unrealized_return_usd: float
    total_return_usd: float
    target_pct: float
    invalidation_pct: float
    target_horizon_minutes: int
    probability_target_before_invalidation_pct: float
    estimated_net_return_pct: float
    probability_rug_like_event_pct: float
    probability_liquidity_failure_pct: float
    probability_sell_route_failure_pct: float
    buy_impact_pct: float | None
    sell_impact_pct: float | None
    round_trip_cost_pct: float | None
    maximum_safe_size_usd: float | None
    catalyst_state: str | None
    catalyst_confidence_pct: float
    catalyst_flow_confirmation: bool
    price_change_since_catalyst_pct: float | None
    recommended_sell_pct: float
    recommended_sell_tokens: float
    expected_net_sell_proceeds_usd: float
    remaining_tokens: float | None
    runner_pct_original: float
    runner_target_pct: float
    moon_bag_value_usd: float
    total_basis_usd: float
    realized_proceeds_usd: float
    unrecovered_principal_usd: float
    tokens_to_recover_principal: float | None
    principal_recovered: bool
    data_confidence_pct: float
    calibration_status: str
    positive_reasons: list[str]
    warnings: list[str]
    blockers: list[str]
    invalidation_conditions: list[str]
    generated_at: int
    quote_observed_at: int | None
    feature_version: str
    policy_version: str
    model_version: str
    why_now: list[str]
    why_not_more: list[str]
    what_changes_action: list[str]
    options: list[dict[str, Any]] = field(default_factory=list)


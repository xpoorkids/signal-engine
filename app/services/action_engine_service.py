from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.models.position import EXIT_STYLE_CATALYST_RUNNER, RISK_PROFILE_AGGRESSIVE
from app.services.db_service import connect_sqlite, resolve_engine_db_path
from app.services.manual_position_service import ManualPositionService
from app.services.x_identity_service import XIdentityService


FEATURE_VERSION = "action-engine-features-v1"
POLICY_VERSION = "aggressive-catalyst-runner-v1"
MODEL_VERSION = "heuristic-shadow-v1"
CALIBRATION_STATUS = "HEURISTIC_UNCALIBRATED"

HARD_SAFETY_BLOCKERS = {
    "no_sell_route",
    "dangerous_token_authority",
    "severe_token_2022_risk",
    "major_liquidity_removal",
    "creator_dumping",
    "connected_insider_dumping",
    "severe_wallet_concentration",
    "confirmed_wash_manipulation",
    "stale_execution_data",
    "invalid_execution_data",
    "impossible_price_impact",
    "hard_contract_safety_failure",
    "operator_blocked_x_identity",
    "blocked_x_rename_lineage",
}

X_IDENTITY_BLOCKERS = {
    "operator_blocked_x_identity",
    "blocked_x_rename_lineage",
    "blocked_x_handle_match_unresolved",
    "stable_x_identity_unresolved",
    "high_risk_x_dev_identity",
}

ACTION_LABELS = [
    "BUY NOW",
    "BUY SMALL",
    "CATALYST BUY NOW",
    "CATALYST BUY SMALL",
    "WAIT",
    "WAIT FOR PULLBACK",
    "DO NOT CHASE",
    "AVOID",
    "HARD FAIL",
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
]


@dataclass(frozen=True)
class AggressiveRunnerPolicy:
    risk_profile: str = RISK_PROFILE_AGGRESSIVE
    exit_style: str = EXIT_STYLE_CATALYST_RUNNER
    execution_mode: str = "manual"
    normal_target_pct: float = 25.0
    normal_invalidation_pct: float = -18.0
    normal_horizon_minutes: int = 15
    catalyst_target_pct: float = 50.0
    catalyst_invalidation_pct: float = -20.0
    catalyst_horizon_minutes: int = 60
    buy_now_probability_min_pct: float = 55.0
    buy_now_net_edge_min_pct: float = 6.0
    buy_now_failure_max_pct: float = 12.0
    buy_now_cost_max_pct: float = 5.0
    buy_now_data_confidence_min_pct: float = 70.0
    buy_small_probability_min_pct: float = 48.0
    buy_small_probability_max_pct: float = 54.9
    buy_small_net_edge_min_pct: float = 2.0
    buy_small_failure_max_pct: float = 15.0
    buy_small_data_confidence_min_pct: float = 60.0
    catalyst_buy_now_probability_min_pct: float = 52.0
    catalyst_buy_now_net_edge_min_pct: float = 10.0
    catalyst_buy_now_confidence_min_pct: float = 75.0
    catalyst_buy_now_cost_max_pct: float = 6.0
    catalyst_buy_small_probability_min_pct: float = 45.0
    catalyst_buy_small_probability_max_pct: float = 51.9
    catalyst_buy_small_net_edge_min_pct: float = 4.0
    normal_extension_chase_pct: float = 25.0
    catalyst_extension_tolerance_pct: float = 40.0
    exit_impact_warning_pct: float = 8.0
    emergency_exit_impact_pct: float = 10.0
    normal_runner_floor_pct: float = 25.0
    catalyst_mixed_runner_floor_pct: float = 35.0
    catalyst_flow_runner_floor_pct: float = 50.0
    catalyst_high_runner_floor_pct: float = 60.0
    normal_trail_pct: float = 22.0
    catalyst_trail_pct: float = 30.0


def action_engine_enabled() -> bool:
    return os.getenv("SIGNAL_ENGINE_ACTION_ENGINE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "y", "on"}


def action_engine_shadow() -> bool:
    return os.getenv("SIGNAL_ENGINE_ACTION_ENGINE_SHADOW", "1").strip().lower() not in {"0", "false", "no", "off"}


def default_risk_profile() -> str:
    return os.getenv("SIGNAL_ENGINE_DEFAULT_RISK_PROFILE", RISK_PROFILE_AGGRESSIVE).strip().lower() or RISK_PROFILE_AGGRESSIVE


def default_exit_style() -> str:
    return os.getenv("SIGNAL_ENGINE_DEFAULT_EXIT_STYLE", EXIT_STYLE_CATALYST_RUNNER).strip().lower() or EXIT_STYLE_CATALYST_RUNNER


def _now() -> int:
    return int(time.time())


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _pct01(value: Any) -> float:
    number = _float(value)
    return number * 100.0 if 0.0 <= number <= 1.0 else number


def _quote_observed_at(features: dict[str, Any]) -> int | None:
    value = features.get("quote_observed_at") or features.get("quote_ts")
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def estimate_entry_features(market: dict[str, Any] | None = None, *, assessment: dict[str, Any] | None = None) -> dict[str, Any]:
    market = market if isinstance(market, dict) else {}
    assessment = assessment if isinstance(assessment, dict) else {}
    manual = assessment.get("manual_buy_assessment") if isinstance(assessment.get("manual_buy_assessment"), dict) else {}
    security = assessment.get("security") if isinstance(assessment.get("security"), dict) else {}
    rug_check = assessment.get("rug_check") if isinstance(assessment.get("rug_check"), dict) else {}
    attention = _pct01(assessment.get("attention_score", manual.get("summary", {}).get("attention_score") if isinstance(manual.get("summary"), dict) else 0))
    risk = _pct01(assessment.get("risk_score", manual.get("summary", {}).get("risk_score") if isinstance(manual.get("summary"), dict) else 0))
    liq = _float(market.get("liquidity_usd"))
    buys = _float(market.get("txns_m5_buys"))
    sells = _float(market.get("txns_m5_sells"))
    buy_ratio = buys / max(sells, 1.0) if buys else 0.0
    vol5 = _float(market.get("volume_m5"))
    price_change = _float(market.get("price_change_m5"))
    hard = []
    if security.get("mint_authority_active") or security.get("freeze_authority_active"):
        hard.append("dangerous_token_authority")
    if str(rug_check.get("verdict") or "").lower() == "high" or manual.get("action") == "HARD_FAIL":
        hard.append("hard_contract_safety_failure")
    if not market:
        hard.append("invalid_execution_data")
    features = {
        "probability_target_before_invalidation_pct": min(72.0, max(25.0, 35.0 + attention * 0.28 + max(0.0, buy_ratio - 1.0) * 6.0 + (8.0 if liq >= 15000 else -8.0))),
        "estimated_net_return_pct": min(35.0, max(-25.0, (attention - risk) * 0.22 + (5.0 if liq >= 15000 else -7.0) + min(price_change, 20.0) * 0.08)),
        "probability_rug_like_event_pct": min(70.0, max(2.0, risk * 0.55 + (8.0 if liq < 5000 else 0.0))),
        "probability_liquidity_failure_pct": min(70.0, 4.0 + (18.0 if liq < 5000 else 8.0 if liq < 15000 else 3.0)),
        "probability_sell_route_failure_pct": 5.0 if liq >= 15000 else 14.0 if liq >= 5000 else 35.0,
        "buy_impact_pct": _float(market.get("buy_impact_pct"), 1.5 if liq >= 15000 else 5.0),
        "sell_impact_pct": _float(market.get("sell_impact_pct"), 2.0 if liq >= 15000 else 7.0),
        "round_trip_cost_pct": _float(market.get("round_trip_cost_pct"), 3.5 if liq >= 15000 else 8.0),
        "maximum_safe_size_usd": max(0.0, min(_float(market.get("maximum_safe_size_usd"), liq * 0.015 if liq else 0.0), 1000.0)),
        "data_confidence_pct": min(95.0, max(20.0, 40.0 + (20.0 if market else 0.0) + (15.0 if liq >= 15000 else 0.0) + (10.0 if buys >= 8 else 0.0))),
        "price_extension_from_preferred_entry_pct": _float(market.get("price_extension_from_preferred_entry_pct"), max(0.0, price_change)),
        "organic_flow_windows": int(_float(market.get("organic_flow_windows"), 2 if buys >= 8 and buy_ratio >= 1.1 else 1 if buys > 0 else 0)),
        "wallet_or_fee_confirmation": _bool(market.get("wallet_or_fee_confirmation", attention >= 65.0 and buys >= 8)),
        "liquidity_deteriorating": _bool(market.get("liquidity_deteriorating", False)),
        "holder_distribution_worsening": _bool(market.get("holder_distribution_worsening", False)),
        "creator_or_insider_selling": _bool(market.get("creator_or_insider_selling", False)),
        "buy_route_ok": _bool(market.get("buy_route_ok", bool(market))),
        "sell_route_ok": _bool(market.get("sell_route_ok", liq > 0)),
        "quote_fresh": _bool(market.get("quote_fresh", True)),
        "hard_safety_blockers": hard + [str(item) for item in market.get("hard_safety_blockers", [])] if isinstance(market.get("hard_safety_blockers"), list) else hard,
    }
    for key in (
        "probability_target_before_invalidation_pct",
        "estimated_net_return_pct",
        "probability_rug_like_event_pct",
        "probability_liquidity_failure_pct",
        "probability_sell_route_failure_pct",
        "buy_impact_pct",
        "sell_impact_pct",
        "round_trip_cost_pct",
        "maximum_safe_size_usd",
        "data_confidence_pct",
        "price_extension_from_preferred_entry_pct",
    ):
        if key in market and market.get(key) is not None:
            features[key] = _float(market.get(key))
    return features


class ActionEngineService:
    def __init__(self, db_path: Path | str | None = None, *, positions: ManualPositionService | None = None, x_identities: XIdentityService | None = None):
        self.db_path = Path(db_path) if db_path is not None else resolve_engine_db_path()
        self.positions = positions or ManualPositionService(self.db_path)
        self.x_identities = x_identities or XIdentityService(self.db_path)
        self.policy = AggressiveRunnerPolicy(risk_profile=default_risk_profile(), exit_style=default_exit_style())

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.db_path)

    def init_schema(self) -> None:
        self.positions.init_schema()
        self.x_identities.init_schema()
        self.x_identities.initialize_seed_blocklist()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS action_recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    position_id TEXT,
                    action TEXT NOT NULL,
                    action_mode TEXT NOT NULL,
                    risk_profile TEXT NOT NULL,
                    exit_style TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    calibration_status TEXT NOT NULL,
                    generated_at INTEGER NOT NULL,
                    outcome_json TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_action_recommendations_token_ts ON action_recommendations(token, generated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_action_recommendations_position_ts ON action_recommendations(position_id, generated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_action_recommendations_action ON action_recommendations(action)")

    def recommend_for_token(
        self,
        token: str,
        *,
        market: dict[str, Any] | None = None,
        assessment: dict[str, Any] | None = None,
        catalyst: dict[str, Any] | None = None,
        intended_size_usd: float | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        self.init_schema()
        position = self.positions.get_open_position_for_token(token)
        if position:
            return self.recommend_for_position(
                position["position_id"],
                market=market,
                catalyst=catalyst,
                intended_size_usd=intended_size_usd,
                persist=persist,
            )
        rec = self._pre_entry_recommendation(
            token,
            market=market or {},
            assessment=assessment or {},
            catalyst=catalyst or self.positions.get_latest_catalyst_for_token(token),
            intended_size_usd=intended_size_usd,
        )
        self._apply_x_identity_pre_entry_guard(rec, market=market or {}, assessment=assessment or {}, catalyst=catalyst)
        if persist:
            self.persist_recommendation(rec)
        return rec

    def recommend_for_position(
        self,
        position_id: str,
        *,
        market: dict[str, Any] | None = None,
        catalyst: dict[str, Any] | None = None,
        intended_size_usd: float | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        self.init_schema()
        position = self.positions.get_position(position_id)
        if not position:
            raise KeyError(position_id)
        catalyst = catalyst or (self.positions.get_catalyst(position["catalyst_id"]) if position.get("catalyst_id") else self.positions.get_latest_catalyst_for_token(position["token"]))
        rec = self._position_recommendation(position, market=market or {}, catalyst=catalyst, intended_size_usd=intended_size_usd)
        self._apply_x_identity_position_guard(rec, market=market or {}, catalyst=catalyst)
        if persist:
            self.persist_recommendation(rec)
        return rec

    def _x_identity_links(self, *, market: dict[str, Any] | None = None, assessment: dict[str, Any] | None = None, catalyst: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []
        for source in (market, assessment, catalyst):
            if not isinstance(source, dict):
                continue
            raw = source.get("x_identity_links") or source.get("x_links") or source.get("x_profiles")
            if isinstance(raw, list):
                links.extend(item for item in raw if isinstance(item, dict))
            social = source.get("x_profile") or source.get("twitter_profile") or source.get("x_url") or source.get("twitter_url")
            if social:
                links.append({"profile_url": social, "link_type": source.get("x_link_type") or "metadata_social", "source": "action_context"})
            handle = source.get("x_handle") or source.get("twitter_handle")
            if handle:
                links.append({"handle": handle, "link_type": source.get("x_link_type") or "metadata_social", "source": "action_context"})
        return links

    def _apply_x_identity_pre_entry_guard(self, rec: dict[str, Any], *, market: dict[str, Any], assessment: dict[str, Any], catalyst: dict[str, Any] | None) -> None:
        decision = self.x_identities.evaluate_token(rec["token"], links=self._x_identity_links(market=market, assessment=assessment, catalyst=catalyst))
        payload = decision.to_dict()
        rec["x_identity_risk"] = payload
        if decision.warnings:
            rec.setdefault("warnings", []).extend(warning for warning in decision.warnings if warning not in rec.setdefault("warnings", []))
        if decision.review_flags:
            rec.setdefault("warnings", []).extend(flag for flag in decision.review_flags if flag not in rec.setdefault("warnings", []))
        if not decision.action:
            return
        rec["blockers"] = list(dict.fromkeys([*rec.get("blockers", []), *decision.blockers]))
        rec["positive_reasons"] = []
        rec["recommended_initial_size_pct"] = 0.0
        rec["display_action"] = decision.action
        rec["action"] = decision.action
        rec["x_identity_block_applied"] = True
        if decision.action == "HARD FAIL":
            rec["display_action"] = "HARD FAIL"
            rec["reason"] = decision.reason
            rec["why_now"] = [
                f"{decision.reason}: {decision.current_handle or 'X identity'} matches an operator-blocked developer identity lineage",
                "positive flow, catalyst, SOL fees, KOL support, wallet score, and momentum were ignored because an operator identity block is active",
            ]
        elif decision.action == "AVOID":
            rec["display_action"] = "AVOID"
            rec["reason"] = decision.reason
            rec["why_now"] = [
                f"{decision.reason}: exact X handle match requires manual identity review",
            ]
        rec["why_not_more"] = ["operator X identity risk blocks positive buy recommendations"]
        rec["what_changes_action"] = ["manual operator review clears the unresolved identity match", "operator disables or removes the block"]

    def _apply_x_identity_position_guard(self, rec: dict[str, Any], *, market: dict[str, Any], catalyst: dict[str, Any] | None) -> None:
        decision = self.x_identities.evaluate_token(rec["token"], links=self._x_identity_links(market=market, catalyst=catalyst))
        payload = decision.to_dict()
        rec["x_identity_risk"] = payload
        if decision.warnings:
            rec.setdefault("warnings", []).extend(warning for warning in decision.warnings if warning not in rec.setdefault("warnings", []))
        if decision.review_flags:
            rec.setdefault("warnings", []).extend(flag for flag in decision.review_flags if flag not in rec.setdefault("warnings", []))
        if not decision.action:
            return
        rec["blockers"] = list(dict.fromkeys([*rec.get("blockers", []), *decision.blockers]))
        if decision.action == "HARD FAIL":
            rec["action"] = "SELL NOW"
            rec["display_action"] = "SELL NOW"
            rec["reason"] = decision.reason
            rec["recommended_sell_pct"] = 100.0
            rec["recommended_sell_tokens"] = rec.get("remaining_tokens") or 0.0
            rec["why_now"] = [f"{decision.reason}: operator-blocked X developer identity is linked to this token"]
            rec["why_not_more"] = ["operator identity block overrides catalyst-runner and aggressive risk preferences"]
            rec["x_identity_block_applied"] = True
        elif decision.action == "AVOID" and rec.get("action") == "ADD SMALL ON CONFIRMATION":
            rec["action"] = "HOLD"
            rec["display_action"] = "HOLD"
            rec["recommended_sell_pct"] = 0.0
            rec["recommended_sell_tokens"] = 0.0
            rec["why_now"] = ["unresolved exact X identity alias match blocks adding until manual review"]
            rec["why_not_more"] = ["operator X identity risk blocks positive add recommendations"]

    def persist_recommendation(self, recommendation: dict[str, Any]) -> str:
        self.init_schema()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO action_recommendations (
                    recommendation_id, token, position_id, action, action_mode, risk_profile,
                    exit_style, payload_json, policy_version, model_version, calibration_status, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recommendation["recommendation_id"],
                    recommendation["token"],
                    recommendation.get("position_id"),
                    recommendation["action"],
                    recommendation["action_mode"],
                    recommendation["risk_profile"],
                    recommendation["exit_style"],
                    _json(recommendation),
                    recommendation["policy_version"],
                    recommendation["model_version"],
                    recommendation["calibration_status"],
                    recommendation["generated_at"],
                ),
            )
        return recommendation["recommendation_id"]

    def record_recommendation_outcome(self, recommendation_id: str, outcome: dict[str, Any]) -> bool:
        self.init_schema()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE action_recommendations SET outcome_json=? WHERE recommendation_id=?",
                (_json(outcome), recommendation_id),
            )
        return cur.rowcount > 0

    def _base_rec(self, token: str, position_id: str | None, action: str, *, intended_size_usd: float | None) -> dict[str, Any]:
        shadow_suffix = " SHADOW" if action_engine_shadow() and action not in {"SELL NOW", "EMERGENCY EXIT", "HARD FAIL", "AVOID"} else ""
        action_mode = "MANUAL DECISION SUPPORT"
        return {
            "recommendation_id": uuid.uuid4().hex,
            "token": token,
            "position_id": position_id,
            "action": action,
            "display_action": f"{action}{shadow_suffix}",
            "action_mode": action_mode,
            "risk_profile": self.policy.risk_profile,
            "exit_style": self.policy.exit_style,
            "execution_mode": "manual",
            "intended_size_usd": intended_size_usd,
            "feature_version": FEATURE_VERSION,
            "policy_version": POLICY_VERSION,
            "model_version": MODEL_VERSION,
            "calibration_status": CALIBRATION_STATUS,
            "generated_at": _now(),
            "not_financial_advice": True,
            "automation": {"executes_trades": False, "requires_private_key": False, "requires_seed_phrase": False, "connects_wallet_for_signing": False},
        }

    def _pre_entry_recommendation(
        self,
        token: str,
        *,
        market: dict[str, Any],
        assessment: dict[str, Any],
        catalyst: dict[str, Any] | None,
        intended_size_usd: float | None,
    ) -> dict[str, Any]:
        f = estimate_entry_features(market, assessment=assessment)
        blockers = list(dict.fromkeys(str(item) for item in f.get("hard_safety_blockers", [])))
        warnings: list[str] = []
        positive: list[str] = []
        why_now: list[str] = []
        why_not_more: list[str] = []
        changes: list[str] = []
        invalidation = ["liquidity falls 20%", "exit impact exceeds 8%", "creator-connected wallet sells", "net flow reverses for two windows"]
        catalyst_state = str((catalyst or {}).get("verification_status") or "").lower() or None
        catalyst_conf = _float((catalyst or {}).get("catalyst_confidence_pct"))
        catalyst_flow = _bool((catalyst or {}).get("catalyst_flow_confirmation"))
        if catalyst and catalyst_state in {"verified", "active", "flow_confirmed", "high_conviction"}:
            positive.append("catalyst_verified")
            why_now.append("catalyst is verified or active")
            invalidation.append("catalyst source retracts or invalidates")
        if f["organic_flow_windows"] >= 2:
            positive.append("organic_flow_confirmed")
            why_now.append("organic flow confirmed in consecutive windows")
        else:
            why_not_more.append("organic flow needs another confirming window")
            changes.append("another independent buyer window confirms")
        if f["wallet_or_fee_confirmation"]:
            positive.append("independent_wallet_or_fee_commitment_confirmation")
            why_now.append("wallet or fee commitment confirmation is present")
        else:
            why_not_more.append("wallet independence or fee commitment is not proven")
        if not f["sell_route_ok"]:
            blockers.append("no_sell_route")
        if not f["buy_route_ok"]:
            blockers.append("no_buy_route")
        if not f["quote_fresh"]:
            blockers.append("stale_execution_data")
        if f["sell_impact_pct"] >= self.policy.emergency_exit_impact_pct or f["buy_impact_pct"] >= self.policy.emergency_exit_impact_pct:
            blockers.append("impossible_price_impact")
        if f["liquidity_deteriorating"]:
            blockers.append("major_liquidity_removal")
        if f["creator_or_insider_selling"]:
            blockers.append("creator_dumping")
        failure = max(f["probability_rug_like_event_pct"], f["probability_liquidity_failure_pct"], f["probability_sell_route_failure_pct"])
        extended = f["price_extension_from_preferred_entry_pct"]
        priced_in = (
            extended >= self.policy.normal_extension_chase_pct
            and f["estimated_net_return_pct"] < self.policy.buy_now_net_edge_min_pct
            and f["organic_flow_windows"] < 2
        )
        catalyst_priced_in = catalyst_state == "priced_in" or (
            catalyst and extended >= self.policy.catalyst_extension_tolerance_pct and f["estimated_net_return_pct"] < self.policy.catalyst_buy_small_net_edge_min_pct
        )

        if any(item in HARD_SAFETY_BLOCKERS or item in {"no_buy_route"} for item in blockers):
            action = "HARD FAIL" if any(item in HARD_SAFETY_BLOCKERS for item in blockers) else "AVOID"
        elif catalyst_priced_in:
            action = "DO NOT CHASE"
            warnings.append("CATALYST PRICED IN")
        elif priced_in:
            action = "DO NOT CHASE"
        elif catalyst and catalyst_state in {"verified", "active", "flow_confirmed", "high_conviction"} and catalyst_flow and catalyst_conf >= self.policy.catalyst_buy_now_confidence_min_pct and f["data_confidence_pct"] >= self.policy.buy_now_data_confidence_min_pct and f["probability_target_before_invalidation_pct"] >= self.policy.catalyst_buy_now_probability_min_pct and f["estimated_net_return_pct"] >= self.policy.catalyst_buy_now_net_edge_min_pct and failure <= self.policy.buy_now_failure_max_pct and f["round_trip_cost_pct"] <= self.policy.catalyst_buy_now_cost_max_pct:
            action = "CATALYST BUY NOW"
        elif catalyst and catalyst_state in {"verified", "active", "flow_confirmed", "high_conviction"} and f["probability_target_before_invalidation_pct"] >= self.policy.catalyst_buy_small_probability_min_pct and f["estimated_net_return_pct"] >= self.policy.catalyst_buy_small_net_edge_min_pct and failure <= self.policy.buy_small_failure_max_pct and f["data_confidence_pct"] >= self.policy.buy_small_data_confidence_min_pct and f["sell_route_ok"]:
            action = "CATALYST BUY SMALL"
        elif f["data_confidence_pct"] >= self.policy.buy_now_data_confidence_min_pct and f["probability_target_before_invalidation_pct"] >= self.policy.buy_now_probability_min_pct and f["estimated_net_return_pct"] >= self.policy.buy_now_net_edge_min_pct and failure <= self.policy.buy_now_failure_max_pct and f["round_trip_cost_pct"] <= self.policy.buy_now_cost_max_pct and f["organic_flow_windows"] >= 2 and f["wallet_or_fee_confirmation"]:
            action = "BUY NOW"
        elif extended >= self.policy.normal_extension_chase_pct and f["organic_flow_windows"] <= 0:
            action = "DO NOT CHASE"
        elif extended >= self.policy.normal_extension_chase_pct and f["estimated_net_return_pct"] < self.policy.buy_now_net_edge_min_pct:
            action = "WAIT FOR PULLBACK"
        elif f["probability_target_before_invalidation_pct"] >= self.policy.buy_small_probability_min_pct and f["estimated_net_return_pct"] >= self.policy.buy_small_net_edge_min_pct and failure <= self.policy.buy_small_failure_max_pct and f["data_confidence_pct"] >= self.policy.buy_small_data_confidence_min_pct and f["sell_route_ok"]:
            action = "BUY SMALL"
        elif f["estimated_net_return_pct"] > 0 and extended >= self.policy.normal_extension_chase_pct:
            action = "WAIT FOR PULLBACK"
        elif failure >= 20.0 or f["estimated_net_return_pct"] < -5.0:
            action = "AVOID"
        else:
            action = "WAIT"

        if f["liquidity_deteriorating"]:
            warnings.append("liquidity is not stable")
        if f["holder_distribution_worsening"]:
            warnings.append("holder distribution worsening")
        if extended >= self.policy.normal_extension_chase_pct:
            warnings.append("price extended from preferred entry")
        if f["round_trip_cost_pct"] and f["round_trip_cost_pct"] > self.policy.buy_now_cost_max_pct:
            why_not_more.append("round-trip execution cost is elevated")
        changes.extend(["sell route improves", "liquidity reaches required floor", "expected net return rises above next threshold"])
        target = self.policy.catalyst_target_pct if action.startswith("CATALYST") else self.policy.normal_target_pct
        invalidation_pct = self.policy.catalyst_invalidation_pct if action.startswith("CATALYST") else self.policy.normal_invalidation_pct
        horizon = self.policy.catalyst_horizon_minutes if action.startswith("CATALYST") else self.policy.normal_horizon_minutes
        rec = self._base_rec(token, None, action, intended_size_usd=intended_size_usd)
        rec.update(
            {
                "position_state": "pre_entry",
                "current_position_value_usd": None,
                "current_executable_return_pct": None,
                "realized_return_usd": 0.0,
                "unrealized_return_usd": 0.0,
                "total_return_usd": 0.0,
                "target_pct": target,
                "invalidation_pct": invalidation_pct,
                "target_horizon_minutes": horizon,
                "probability_target_before_invalidation_pct": round(f["probability_target_before_invalidation_pct"], 2),
                "probability_catalyst_target_before_invalidation_pct": round(f["probability_target_before_invalidation_pct"], 2) if catalyst else None,
                "estimated_net_return_pct": round(f["estimated_net_return_pct"], 2),
                "probability_rug_like_event_pct": round(f["probability_rug_like_event_pct"], 2),
                "probability_liquidity_failure_pct": round(f["probability_liquidity_failure_pct"], 2),
                "probability_sell_route_failure_pct": round(f["probability_sell_route_failure_pct"], 2),
                "buy_impact_pct": round(f["buy_impact_pct"], 2),
                "sell_impact_pct": round(f["sell_impact_pct"], 2),
                "round_trip_cost_pct": round(f["round_trip_cost_pct"], 2),
                "maximum_safe_size_usd": round(f["maximum_safe_size_usd"], 2),
                "price_extension_from_preferred_entry_pct": round(extended, 2),
                "catalyst_state": catalyst_state,
                "catalyst_confidence_pct": catalyst_conf,
                "catalyst_flow_confirmation": catalyst_flow,
                "price_change_since_catalyst_pct": (catalyst or {}).get("price_change_since_catalyst_pct"),
                "recommended_initial_size_pct": 100.0 if action in {"BUY NOW", "CATALYST BUY NOW"} else 40.0 if action == "BUY SMALL" else 50.0 if action == "CATALYST BUY SMALL" else 0.0,
                "recommended_sell_pct": 0.0,
                "recommended_sell_tokens": 0.0,
                "expected_net_sell_proceeds_usd": 0.0,
                "remaining_tokens": None,
                "runner_pct_original": 0.0,
                "runner_target_pct": self.runner_target_pct(catalyst),
                "moon_bag_value_usd": 0.0,
                "total_basis_usd": 0.0,
                "realized_proceeds_usd": 0.0,
                "unrecovered_principal_usd": 0.0,
                "tokens_to_recover_principal": None,
                "principal_recovered": False,
                "data_confidence_pct": round(f["data_confidence_pct"], 2),
                "quote_observed_at": _quote_observed_at(market),
                "positive_reasons": positive,
                "warnings": warnings,
                "blockers": list(dict.fromkeys(blockers)),
                "invalidation_conditions": invalidation,
                "why_now": why_now or ["setup is being evaluated against executable risk"],
                "why_not_more": why_not_more or ["buy/profit actions are shadow and uncalibrated"],
                "what_changes_action": list(dict.fromkeys(changes)),
            }
        )
        return rec

    def _position_recommendation(
        self,
        position: dict[str, Any],
        *,
        market: dict[str, Any],
        catalyst: dict[str, Any] | None,
        intended_size_usd: float | None,
    ) -> dict[str, Any]:
        quote_value = _float(market.get("current_executable_value_usd"), _float(position.get("current_executable_position_value_usd")))
        if quote_value:
            position = self.positions.update_executable_value(position["position_id"], executable_value_usd=quote_value, quote_observed_at=_quote_observed_at(market))
        total_basis = _float(position.get("total_cash_invested_usd"))
        realized = _float(position.get("realized_proceeds_usd"))
        current_qty = _float(position.get("current_token_quantity"))
        original_qty = _float(position.get("original_token_quantity"))
        current_value = _float(position.get("current_executable_position_value_usd"), quote_value)
        executable_net_per_token = _float(market.get("executable_net_sell_value_per_token"), current_value / current_qty if current_qty else 0.0)
        total_return = realized + current_value - total_basis
        current_return_pct = (total_return / total_basis * 100.0) if total_basis else 0.0
        realized_return = realized - total_basis
        unrealized_return = current_value
        unrecovered = max(0.0, total_basis - realized)
        tokens_to_principal = calculate_tokens_to_recover_principal(unrecovered, executable_net_per_token)
        runner_target = self.runner_target_pct(catalyst)
        runner_floor_tokens = original_qty * runner_target / 100.0
        runner_pct_original = (current_qty / original_qty * 100.0) if original_qty else 0.0
        catalyst_state = str((catalyst or {}).get("verification_status") or "").lower() or None
        catalyst_conf = _float((catalyst or {}).get("catalyst_confidence_pct"))
        catalyst_flow = _bool((catalyst or {}).get("catalyst_flow_confirmation"))
        sell_impact = _float(market.get("sell_impact_pct"), 0.0)
        liq_down_2m = _float(market.get("liquidity_change_2m_pct"), 0.0)
        liq_down_5m = _float(market.get("liquidity_change_5m_pct"), 0.0)
        continuation = _float(market.get("probability_continued_upside_pct"), 50.0 + (10.0 if catalyst_flow else 0.0) - max(0.0, -_float(position.get("drawdown_from_executable_peak_pct"))) * 0.4)
        reversal = _float(market.get("probability_major_reversal_pct"), 100.0 - continuation)
        liquidity_failure = _float(market.get("probability_liquidity_failure_pct"), 6.0 if current_value else 18.0)
        sell_route_failure = _float(market.get("probability_sell_route_failure_pct"), 5.0 if market.get("sell_route_ok", True) else 80.0)
        hard_blockers = [str(item) for item in market.get("hard_safety_blockers", [])] if isinstance(market.get("hard_safety_blockers"), list) else []
        if not _bool(market.get("sell_route_ok", True)):
            hard_blockers.append("no_sell_route")
        if sell_impact >= self.policy.emergency_exit_impact_pct:
            hard_blockers.append("impossible_price_impact")
        if liq_down_2m <= -20.0 or liq_down_5m <= -35.0:
            hard_blockers.append("major_liquidity_removal")
        if _bool(market.get("creator_or_insider_selling", False)):
            hard_blockers.append("creator_dumping")
        if catalyst_state in {"invalidated", "false_or_retracted"}:
            action = "CATALYST INVALIDATED"
        elif catalyst_state == "weakening":
            action = "CATALYST WEAKENING"
        else:
            action = "HOLD"
        recommended_sell_tokens = 0.0
        recommended_sell_pct = 0.0
        if any(item in HARD_SAFETY_BLOCKERS for item in hard_blockers):
            action = "EMERGENCY EXIT"
            recommended_sell_tokens = current_qty
            recommended_sell_pct = 100.0
        elif catalyst_state in {"invalidated", "false_or_retracted"} and (continuation < 40.0 or _bool(market.get("flow_reversing", False))):
            action = "SELL NOW"
            recommended_sell_tokens = current_qty
            recommended_sell_pct = 100.0
        elif unrecovered > 0 and tokens_to_principal is not None and current_return_pct >= 100.0:
            action = "RECOVER PRINCIPAL"
            recommended_sell_tokens = min(current_qty, tokens_to_principal)
            recommended_sell_pct = recommended_sell_tokens / current_qty * 100.0 if current_qty else 0.0
        elif current_return_pct >= 200.0 and current_qty > runner_floor_tokens:
            action = "TRIM"
            recommended_sell_pct = 12.5
            recommended_sell_tokens = min(current_qty - runner_floor_tokens, current_qty * recommended_sell_pct / 100.0)
        elif current_return_pct >= 50.0 and (not catalyst_flow or continuation < 60.0) and current_qty > runner_floor_tokens:
            action = "TAKE PROFIT"
            recommended_sell_pct = 15.0 if not catalyst else 10.0
            recommended_sell_tokens = min(current_qty - runner_floor_tokens, current_qty * recommended_sell_pct / 100.0)
        elif current_return_pct >= 25.0 and not catalyst_flow and current_qty > runner_floor_tokens:
            action = "TAKE PROFIT"
            recommended_sell_pct = 12.5
            recommended_sell_tokens = min(current_qty - runner_floor_tokens, current_qty * recommended_sell_pct / 100.0)
        elif unrecovered <= 0 and catalyst_state in {"active", "flow_confirmed", "high_conviction"}:
            action = "HOLD MOON BAG"
        elif unrecovered <= 0:
            action = "HOLD RUNNER"
        elif _bool(market.get("positive_new_confirmation", False)) and current_return_pct < 25.0 and not _bool(market.get("liquidity_deteriorating", False)) and not _bool(market.get("creator_or_insider_selling", False)):
            action = "ADD SMALL ON CONFIRMATION"

        expected_proceeds = recommended_sell_tokens * executable_net_per_token
        remaining_tokens = max(0.0, current_qty - recommended_sell_tokens)
        options = []
        if action == "RECOVER PRINCIPAL" and remaining_tokens < runner_floor_tokens:
            preserve_tokens_to_sell = max(0.0, current_qty - runner_floor_tokens)
            options = [
                {
                    "label": "Recover Principal",
                    "sell_tokens": recommended_sell_tokens,
                    "estimated_net_proceeds_usd": expected_proceeds,
                    "remaining_tokens": remaining_tokens,
                    "principal_remaining_usd": 0.0 if expected_proceeds >= unrecovered else unrecovered - expected_proceeds,
                    "runner_pct_original": (remaining_tokens / original_qty * 100.0) if original_qty else 0.0,
                },
                {
                    "label": "Preserve Larger Moon Bag",
                    "sell_tokens": preserve_tokens_to_sell,
                    "estimated_net_proceeds_usd": preserve_tokens_to_sell * executable_net_per_token,
                    "remaining_tokens": current_qty - preserve_tokens_to_sell,
                    "principal_remaining_usd": max(0.0, unrecovered - preserve_tokens_to_sell * executable_net_per_token),
                    "runner_pct_original": runner_target,
                    "additional_risk_accepted": True,
                },
            ]
        trail = self.policy.catalyst_trail_pct if catalyst_state in {"verified", "active", "flow_confirmed", "high_conviction"} else self.policy.normal_trail_pct
        why_now = []
        if action in {"TAKE PROFIT", "TRIM"}:
            why_now.append("executable profit target reached while preserving runner floor")
        if action == "RECOVER PRINCIPAL":
            why_now.append("current executable quote can recover remaining principal")
        if action == "HOLD MOON BAG":
            why_now.append("principal recovered and catalyst remains active")
        if action == "EMERGENCY EXIT":
            why_now.append("hard safety condition overrides moon-bag target")
        if action == "ADD SMALL ON CONFIRMATION":
            why_now.append("new positive confirmation appeared without safety deterioration")
        rec = self._base_rec(position["token"], position["position_id"], action, intended_size_usd=intended_size_usd)
        rec.update(
            {
                "position_state": "owned",
                "current_position_value_usd": round(current_value, 6),
                "current_executable_return_pct": round(current_return_pct, 4),
                "realized_return_usd": round(realized_return, 6),
                "unrealized_return_usd": round(unrealized_return, 6),
                "total_return_usd": round(total_return, 6),
                "target_pct": self.policy.catalyst_target_pct if catalyst else self.policy.normal_target_pct,
                "invalidation_pct": self.policy.catalyst_invalidation_pct if catalyst else self.policy.normal_invalidation_pct,
                "target_horizon_minutes": self.policy.catalyst_horizon_minutes if catalyst else self.policy.normal_horizon_minutes,
                "probability_target_before_invalidation_pct": continuation,
                "estimated_net_return_pct": max(-100.0, continuation - reversal),
                "probability_rug_like_event_pct": _float(market.get("probability_rug_like_event_pct"), 5.0),
                "probability_liquidity_failure_pct": liquidity_failure,
                "probability_sell_route_failure_pct": sell_route_failure,
                "buy_impact_pct": None,
                "sell_impact_pct": sell_impact,
                "round_trip_cost_pct": _float(market.get("round_trip_cost_pct"), None),
                "maximum_safe_size_usd": _float(market.get("maximum_safe_size_usd"), None),
                "catalyst_state": catalyst_state,
                "catalyst_confidence_pct": catalyst_conf,
                "catalyst_flow_confirmation": catalyst_flow,
                "price_change_since_catalyst_pct": (catalyst or {}).get("price_change_since_catalyst_pct"),
                "recommended_sell_pct": round(recommended_sell_pct, 4),
                "recommended_sell_tokens": recommended_sell_tokens,
                "expected_net_sell_proceeds_usd": round(expected_proceeds, 6),
                "remaining_tokens": round(remaining_tokens, 10),
                "runner_pct_original": round((remaining_tokens / original_qty * 100.0) if original_qty else 0.0, 4),
                "runner_target_pct": runner_target,
                "moon_bag_value_usd": round(remaining_tokens * executable_net_per_token, 6),
                "recommended_executable_trailing_distance_pct": trail,
                "probability_continued_upside_pct": continuation,
                "probability_major_reversal_pct": reversal,
                "current_executable_value_usd": current_value,
                "highest_executable_value_usd": position.get("highest_executable_position_value_usd"),
                "drawdown_from_executable_peak_pct": position.get("drawdown_from_executable_peak_pct"),
                "original_tokens_remaining_pct": round((current_qty / original_qty * 100.0) if original_qty else 0.0, 4),
                "total_basis_usd": total_basis,
                "realized_proceeds_usd": realized,
                "unrecovered_principal_usd": unrecovered,
                "tokens_to_recover_principal": tokens_to_principal,
                "principal_recovered": unrecovered <= 1e-9,
                "data_confidence_pct": _float(market.get("data_confidence_pct"), 75.0 if current_value else 50.0),
                "quote_observed_at": _quote_observed_at(market),
                "positive_reasons": ["sell route healthy"] if not hard_blockers else [],
                "warnings": [str(item) for item in market.get("warnings", [])] if isinstance(market.get("warnings"), list) else [],
                "blockers": list(dict.fromkeys(hard_blockers)),
                "invalidation_conditions": position.get("invalidation_conditions") or ["liquidity falls 20%", "exit impact exceeds 8%", "creator-connected wallet sells"],
                "why_now": why_now or ["hold while no stronger action is justified"],
                "why_not_more": ["runner floor must be preserved", "buy/profit actions are shadow and uncalibrated"],
                "what_changes_action": ["flow reverses", "sell route deteriorates", "liquidity changes materially", "catalyst state changes"],
                "options": options,
            }
        )
        return rec

    def runner_target_pct(self, catalyst: dict[str, Any] | None) -> float:
        if not catalyst:
            return self.policy.normal_runner_floor_pct
        state = str(catalyst.get("verification_status") or "").lower()
        flow = _bool(catalyst.get("catalyst_flow_confirmation"))
        if state == "high_conviction" and flow:
            return self.policy.catalyst_high_runner_floor_pct
        if state in {"flow_confirmed", "active"} and flow:
            return self.policy.catalyst_flow_runner_floor_pct
        if state in {"verified", "active"}:
            return self.policy.catalyst_mixed_runner_floor_pct
        if state in {"invalidated", "false_or_retracted", "expired", "weakening"}:
            return self.policy.normal_runner_floor_pct
        return self.policy.normal_runner_floor_pct


def calculate_tokens_to_recover_principal(remaining_unrecovered_principal_usd: float, executable_net_sell_value_per_token: float | None) -> float | None:
    remaining = max(0.0, _float(remaining_unrecovered_principal_usd))
    per_token = _float(executable_net_sell_value_per_token)
    if remaining <= 0.0:
        return 0.0
    if per_token <= 0.0:
        return None
    return remaining / per_token

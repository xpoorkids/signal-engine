from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import itertools
import math
import random
from typing import Any, Callable, Iterable

from worker.alert_gate import admission_check_candidate
from worker.signal_policy import (
    AttentionScoringPolicy,
    CandidateSignalPolicy,
    RouteSignalPolicy,
    attention_scoring_policy,
    candidate_signal_policy,
    route_signal_policy,
)


SelectorFn = Callable[[dict[str, Any], dict[str, Any]], bool]


@dataclass(frozen=True)
class SearchMetrics:
    selected: int
    total: int
    expectancy: float
    win_rate: float
    precision: float
    false_positive_rate: float
    max_drawdown: float
    stability: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchResult:
    params: dict[str, Any]
    metrics: SearchMetrics
    rank_key: tuple[float, float, float, float, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "metrics": self.metrics.as_dict(),
            "rank_key": list(self.rank_key),
        }


@dataclass(frozen=True)
class SignalSweepDecision:
    predicted_route: str
    candidate_gate_pass: bool
    candidate_send_eligible: bool
    candidate_confirmations: list[str]
    candidate_reasons: list[str]
    route_tier: str
    route_confidence: float
    route_confirmations: list[str]
    route_blockers: list[str]
    heating_allowed: bool
    heating_reasons: list[str]
    score_pass: bool
    score_value: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalSweepMetrics:
    selected: int
    total: int
    expectancy: float
    win_rate: float
    precision: float
    false_positive_rate: float
    max_drawdown: float
    stability: float
    avg_quality: float
    route_accuracy: float
    candidate_precision: float
    heating_precision: float
    sniper_precision: float
    robustness: float
    candidate_selected: int
    heating_selected: int
    sniper_selected: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalSweepResult:
    params: dict[str, Any]
    metrics: SignalSweepMetrics
    rank_key: tuple[float, float, float, float, float, float, int]
    route_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "metrics": self.metrics.as_dict(),
            "rank_key": list(self.rank_key),
            "route_counts": dict(self.route_counts),
        }


def expand_parameter_space(parameter_space: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(parameter_space.keys())
    values = [list(parameter_space[key]) for key in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def sample_parameter_space(
    parameter_space: dict[str, list[Any]],
    *,
    sample_size: int,
    seed: int = 42,
) -> list[dict[str, Any]]:
    all_sets = expand_parameter_space(parameter_space)
    if sample_size >= len(all_sets):
        return all_sets
    rng = random.Random(seed)
    return rng.sample(all_sets, sample_size)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _sequence_max_drawdown(values: Iterable[float]) -> float:
    peak = 0.0
    equity = 0.0
    drawdown = 0.0
    for item in values:
        equity += float(item)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def score_selected_outcomes(selected_records: list[dict[str, Any]], *, total_records: int) -> SearchMetrics:
    selected = len(selected_records)
    if selected == 0:
        return SearchMetrics(
            selected=0,
            total=total_records,
            expectancy=0.0,
            win_rate=0.0,
            precision=0.0,
            false_positive_rate=0.0,
            max_drawdown=0.0,
            stability=0.0,
        )

    pnl_values = [_safe_float(item.get("pnl_pct"), _safe_float(item.get("pnl"), 0.0)) for item in selected_records]
    winners = sum(1 for item in pnl_values if item > 0)
    losers = sum(1 for item in pnl_values if item <= 0)
    expectancy = sum(pnl_values) / max(1, selected)
    win_rate = winners / max(1, selected)
    precision = win_rate
    false_positive_rate = losers / max(1, selected)
    max_drawdown = _sequence_max_drawdown(pnl_values)

    by_dataset: dict[str, list[float]] = {}
    for item, pnl in zip(selected_records, pnl_values):
        dataset = str(item.get("dataset") or "default")
        by_dataset.setdefault(dataset, []).append(pnl)
    grouped_expectancies = [sum(items) / max(1, len(items)) for items in by_dataset.values()]
    if len(grouped_expectancies) <= 1:
        stability = 1.0
    else:
        mean = sum(grouped_expectancies) / len(grouped_expectancies)
        variance = sum((value - mean) ** 2 for value in grouped_expectancies) / len(grouped_expectancies)
        stddev = math.sqrt(max(variance, 0.0))
        denom = max(abs(mean), 1.0)
        stability = max(0.0, 1.0 - min(1.0, stddev / denom))

    return SearchMetrics(
        selected=selected,
        total=total_records,
        expectancy=round(expectancy, 6),
        win_rate=round(win_rate, 6),
        precision=round(precision, 6),
        false_positive_rate=round(false_positive_rate, 6),
        max_drawdown=round(max_drawdown, 6),
        stability=round(stability, 6),
    )


def rank_search_results(results: list[SearchResult]) -> list[SearchResult]:
    return sorted(results, key=lambda item: item.rank_key, reverse=True)


def run_parameter_search(
    *,
    records: list[dict[str, Any]],
    parameter_space: dict[str, list[Any]],
    selector: SelectorFn,
    mode: str = "grid",
    sample_size: int | None = None,
    seed: int = 42,
) -> list[SearchResult]:
    if mode not in {"grid", "random"}:
        raise ValueError("unsupported_search_mode")
    if mode == "grid":
        parameter_sets = expand_parameter_space(parameter_space)
    else:
        parameter_sets = sample_parameter_space(
            parameter_space,
            sample_size=max(1, int(sample_size or 1)),
            seed=seed,
        )

    results: list[SearchResult] = []
    for params in parameter_sets:
        selected_records = [record for record in records if selector(record, params)]
        metrics = score_selected_outcomes(selected_records, total_records=len(records))
        rank_key = (
            metrics.expectancy,
            metrics.precision,
            -metrics.max_drawdown,
            metrics.stability,
            metrics.selected,
        )
        results.append(SearchResult(params=dict(params), metrics=metrics, rank_key=rank_key))
    return rank_search_results(results)


def build_signal_parameter_space() -> dict[str, list[Any]]:
    candidate = candidate_signal_policy()
    route = route_signal_policy()
    scoring = attention_scoring_policy()
    return {
        "candidate.min_confirmation_signals": [
            max(1, candidate.min_confirmation_signals - 1),
            candidate.min_confirmation_signals,
            candidate.min_confirmation_signals + 1,
        ],
        "candidate.min_send_confirmation_signals": [
            max(1, candidate.min_send_confirmation_signals - 1),
            candidate.min_send_confirmation_signals,
            candidate.min_send_confirmation_signals + 1,
        ],
        "candidate.strong_attention_threshold": [
            round(max(0.1, candidate.strong_attention_threshold - 0.05), 3),
            round(candidate.strong_attention_threshold, 3),
            round(min(0.95, candidate.strong_attention_threshold + 0.05), 3),
        ],
        "route.sniper_min_attention": [
            round(max(0.1, route.sniper_min_attention - 0.05), 3),
            round(route.sniper_min_attention, 3),
            round(min(0.95, route.sniper_min_attention + 0.05), 3),
        ],
        "route.sniper_min_confirmations": [
            max(1, route.sniper_min_confirmations - 1),
            route.sniper_min_confirmations,
            route.sniper_min_confirmations + 1,
        ],
        "route.heating_min_confirmations": [
            max(1, route.heating_min_confirmations - 1),
            route.heating_min_confirmations,
            route.heating_min_confirmations + 1,
        ],
        "route.heating_delivery_min_confidence": [
            round(max(0.1, route.heating_delivery_min_confidence - 0.05), 3),
            round(route.heating_delivery_min_confidence, 3),
            round(min(0.95, route.heating_delivery_min_confidence + 0.08), 3),
        ],
        "score.attention_weight": [0.20, 0.24, 0.28],
        "score.elite_weight": [0.18, 0.22, 0.26],
        "score.unique_weight": [0.12, 0.16, 0.20],
        "score.burst_weight": [0.12, 0.16, 0.20],
        "score.confirmation_weight": [0.08, 0.12, 0.16],
        "score.flow_bonus": [0.04, 0.06, 0.08],
        "score.quality_bonus": [0.02, 0.04, 0.06],
        "score.min_confidence": [
            round(max(0.1, route.heating_delivery_min_confidence - 0.05), 3),
            round(route.heating_delivery_min_confidence, 3),
            round(min(0.95, route.heating_delivery_min_confidence + 0.08), 3),
        ],
        "score.tracked_wallet_step": [
            round(max(0.01, scoring.tracked_wallet_step - 0.03), 3),
            round(scoring.tracked_wallet_step, 3),
            round(scoring.tracked_wallet_step + 0.03, 3),
        ],
    }


def _split_signal_overrides(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    candidate_overrides: dict[str, Any] = {}
    route_overrides: dict[str, Any] = {}
    scoring_overrides: dict[str, Any] = {}
    gate_overrides: dict[str, Any] = {}
    for key, value in params.items():
        if key.startswith("candidate."):
            candidate_overrides[key.split(".", 1)[1]] = value
        elif key.startswith("route."):
            route_overrides[key.split(".", 1)[1]] = value
        elif key.startswith("score."):
            scoring_overrides[key.split(".", 1)[1]] = value
        elif key.startswith("gate."):
            gate_overrides[key.split(".", 1)[1]] = value
    return candidate_overrides, route_overrides, scoring_overrides, gate_overrides


def _policy_with_overrides(policy: Any, overrides: dict[str, Any]) -> Any:
    if not overrides:
        return policy
    return replace(policy, **{key: value for key, value in overrides.items() if hasattr(policy, key)})


def _snapshot_metrics(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    metrics = record.get("attention_metrics") if isinstance(record.get("attention_metrics"), dict) else {}
    dex_summary = record.get("dex_summary") if isinstance(record.get("dex_summary"), dict) else {}
    base_metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    metrics = dict(metrics)
    metrics.setdefault("unique_buyers_5m", record.get("unique_buyers_5m"))
    metrics.setdefault("unique_buyers_15m", record.get("unique_buyers_15m"))
    metrics.setdefault("burst_count_60s", record.get("burst_count_60s"))
    metrics.setdefault("tracked_wallet_hits", record.get("tracked_wallet_hits"))
    metrics.setdefault("kol_wallet_hits", record.get("kol_wallet_hits"))
    metrics.setdefault("x_tweet_count", record.get("x_tweet_count"))
    metrics.setdefault("x_unique_authors", record.get("x_unique_authors"))
    metrics.setdefault("top_wallet_share_30s", record.get("top_wallet_share_30s"))
    metrics.setdefault("unique_wallets_30s", record.get("unique_wallets_30s"))
    metrics.setdefault("narrative_hits", record.get("narrative_hits"))

    dex_summary = dict(dex_summary)
    dex_summary.setdefault("liquidity_usd", record.get("liquidity_usd"))
    dex_summary.setdefault("txns_m5_buys", record.get("txns_m5_buys"))
    dex_summary.setdefault("txns_m5_sells", record.get("txns_m5_sells"))
    dex_summary.setdefault("age_minutes", record.get("age_minutes"))
    dex_summary.setdefault("volume_m5", record.get("volume_m5"))
    dex_summary.setdefault("price_change_m5", record.get("price_change_m5"))

    base_metrics = dict(base_metrics)
    base_metrics.setdefault("age_minutes", record.get("age_minutes"))
    return metrics, dex_summary, base_metrics


def _candidate_confirmation_signals_for_policy(
    *,
    attention_score: float,
    metrics: dict[str, Any],
    dex_summary: dict[str, Any] | None,
    policy: CandidateSignalPolicy,
) -> tuple[list[str], list[str]]:
    if not metrics and not dex_summary:
        return [], []
    reasons: list[str] = []
    confirmations: list[str] = []

    buyers_5m = _safe_int(metrics.get("unique_buyers_5m"))
    burst_60s = _safe_int(metrics.get("burst_count_60s"))
    tracked_hits = _safe_int(metrics.get("tracked_wallet_hits"))
    kol_hits = _safe_int(metrics.get("kol_wallet_hits"))
    x_mentions = _safe_int(metrics.get("x_tweet_count"))
    x_authors = _safe_int(metrics.get("x_unique_authors"))
    narrative_hits = metrics.get("narrative_hits") if isinstance(metrics.get("narrative_hits"), list) else []
    top_wallet_share = _safe_float(metrics.get("top_wallet_share_30s"))
    unique_wallets_30s = _safe_int(metrics.get("unique_wallets_30s"))

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
    if x_mentions >= policy.social_support_min_mentions and x_authors >= policy.social_support_min_authors:
        confirmations.append("social_support")
    if narrative_hits:
        confirmations.append("narrative_alignment")

    liq = _safe_float((dex_summary or {}).get("liquidity_usd"))
    buys5m = _safe_int((dex_summary or {}).get("txns_m5_buys"))
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
        reasons.append(f"confirmation_signals<{policy.min_confirmation_signals}")
    return reasons, confirmations


def _candidate_send_reasons_for_policy(
    *,
    attention_score: float | None,
    creator_score: float,
    metrics: dict[str, Any],
    dex_summary: dict[str, Any] | None,
    policy: CandidateSignalPolicy,
) -> tuple[bool, list[str], list[str]]:
    attn = _safe_float(attention_score)
    reasons, confirmations = _candidate_confirmation_signals_for_policy(
        attention_score=attn,
        metrics=metrics,
        dex_summary=dex_summary,
        policy=policy,
    )
    confirmation_set = set(confirmations)
    quality_confirmed = bool(
        {"tracked_wallet_flow", "kol_wallet_flow", "market_support", "social_support", "narrative_alignment"} & confirmation_set
    )
    has_creator_support = creator_score >= policy.strong_creator_threshold and attn >= policy.creator_attention_floor
    has_attention_only = attn >= policy.strong_attention_threshold
    has_balanced_quality = creator_score >= policy.creator_attention_target and attn >= policy.creator_attention_target

    send_reasons = list(reasons)
    if len(confirmations) < policy.min_send_confirmation_signals and attn < policy.exceptional_attention_threshold:
        send_reasons.append(f"send_confirmation_signals<{policy.min_send_confirmation_signals}")
    if not quality_confirmed and attn < policy.exceptional_attention_threshold:
        send_reasons.append("quality_confirmation_missing")
    eligible = (has_attention_only or has_creator_support or has_balanced_quality) and not send_reasons
    if not (has_attention_only or has_creator_support or has_balanced_quality):
        send_reasons.append("attention_creator_alignment_missing")
    return eligible, send_reasons, confirmations


def _route_decision_for_policy(
    *,
    attention_score: float | None,
    elite_score: int,
    unique_10s: int,
    burst_10s: int,
    hard_fail_from_authority_checks: bool,
    metrics: dict[str, Any],
    dex_summary: dict[str, Any] | None,
    route_policy: RouteSignalPolicy,
    scoring_policy: AttentionScoringPolicy,
    score_weights: dict[str, float],
) -> dict[str, Any]:
    liq = _safe_float((dex_summary or {}).get("liquidity_usd"))
    buys5m = _safe_int((dex_summary or {}).get("txns_m5_buys"))
    tracked_hits = _safe_int(metrics.get("tracked_wallet_hits"))
    kol_hits = _safe_int(metrics.get("kol_wallet_hits"))
    buyers_5m = _safe_int(metrics.get("unique_buyers_5m"))
    burst_60s = _safe_int(metrics.get("burst_count_60s"))
    x_mentions = _safe_int(metrics.get("x_tweet_count"))
    x_authors = _safe_int(metrics.get("x_unique_authors"))
    attention = _safe_float(attention_score)

    confirmations: list[str] = []
    blockers: list[str] = []
    sniper_blockers: list[str] = []
    route_tier = "watch"

    if tracked_hits > 0:
        confirmations.append("tracked_wallet_flow")
    if kol_hits > 0:
        confirmations.append("kol_wallet_flow")
    if buyers_5m >= route_policy.route_buyer_breadth_min:
        confirmations.append("buyer_breadth")
    if burst_60s >= route_policy.route_burst_strength_min:
        confirmations.append("burst_strength")
    if liq >= route_policy.heating_min_liq_usd and buys5m >= route_policy.route_market_support_min_buys5m:
        confirmations.append("market_support")
    if attention >= route_policy.heating_min_attention:
        confirmations.append("attention_support")
    if x_mentions >= route_policy.heating_min_x_mentions and x_authors >= route_policy.heating_min_x_authors:
        confirmations.append("social_support")
    if tracked_hits > 0:
        attention = min(1.0, attention + _safe_float(score_weights.get("tracked_wallet_step"), scoring_policy.tracked_wallet_step))

    flow_confirmations = [item for item in confirmations if item in {"buyer_breadth", "burst_strength"}]
    quality_confirmations = [item for item in confirmations if item in {"tracked_wallet_flow", "kol_wallet_flow", "market_support", "social_support"}]
    core_sniper_met = (
        unique_10s >= route_policy.sniper_min_unique_10s
        and burst_10s >= route_policy.sniper_min_burst_10s
        and elite_score >= route_policy.sniper_min_elite
        and attention >= route_policy.sniper_min_attention
    )
    sniper_ready = (
        core_sniper_met
        and len(confirmations) >= route_policy.sniper_min_confirmations
        and bool(flow_confirmations)
        and bool(quality_confirmations)
    )
    sniper_fast_track = (
        sniper_ready
        and attention >= route_policy.sniper_fast_track_attention
        and len(confirmations) >= route_policy.sniper_fast_track_confirmations
    )
    route_confidence = min(
        1.0,
        (
            min(attention / max(route_policy.sniper_min_attention, 0.01), 1.2) * _safe_float(score_weights.get("attention_weight"), 0.24)
            + min(max(elite_score, 0) / max(route_policy.sniper_min_elite, 1), 1.2) * _safe_float(score_weights.get("elite_weight"), 0.22)
            + min(unique_10s / max(route_policy.sniper_min_unique_10s, 1), 1.4) * _safe_float(score_weights.get("unique_weight"), 0.16)
            + min(burst_10s / max(route_policy.sniper_min_burst_10s, 1), 1.4) * _safe_float(score_weights.get("burst_weight"), 0.16)
            + min(len(confirmations) / max(route_policy.sniper_fast_track_confirmations, 1), 1.2) * _safe_float(score_weights.get("confirmation_weight"), 0.12)
            + (_safe_float(score_weights.get("flow_bonus"), 0.06) if flow_confirmations else 0.0)
            + (_safe_float(score_weights.get("quality_bonus"), 0.04) if quality_confirmations else 0.0)
        ),
    )

    if hard_fail_from_authority_checks:
        blockers.append("authority_hard_fail")
        sniper_blockers.append("authority_hard_fail")
    if unique_10s < route_policy.sniper_min_unique_10s:
        sniper_blockers.append(f"unique_10s<{route_policy.sniper_min_unique_10s}")
    if burst_10s < route_policy.sniper_min_burst_10s:
        sniper_blockers.append(f"burst_10s<{route_policy.sniper_min_burst_10s}")
    if elite_score < route_policy.sniper_min_elite:
        sniper_blockers.append(f"elite<{route_policy.sniper_min_elite}")
    if attention < route_policy.sniper_min_attention:
        sniper_blockers.append(f"sniper_attention<{route_policy.sniper_min_attention:.2f}")
    if len(confirmations) < route_policy.sniper_min_confirmations:
        sniper_blockers.append(f"sniper_confirmations<{route_policy.sniper_min_confirmations}")
    if not flow_confirmations:
        sniper_blockers.append("sniper_flow_confirmation_missing")
    if not quality_confirmations:
        sniper_blockers.append("sniper_quality_confirmation_missing")

    if not blockers and sniper_ready:
        route_tier = "sniper"
    elif (
        not blockers
        and len(confirmations) >= route_policy.heating_min_confirmations
        and (
            attention >= route_policy.heating_min_attention
            or elite_score >= max(7, route_policy.sniper_min_elite - 1)
            or tracked_hits > 0
            or kol_hits > 0
        )
        and (bool(flow_confirmations) or bool(quality_confirmations))
    ):
        route_tier = "heating_up"
        if core_sniper_met and not sniper_ready:
            blockers.extend(item for item in sniper_blockers if item not in blockers)
    else:
        if unique_10s < route_policy.sniper_min_unique_10s:
            blockers.append(f"unique_10s<{route_policy.sniper_min_unique_10s}")
        if burst_10s < route_policy.sniper_min_burst_10s:
            blockers.append(f"burst_10s<{route_policy.sniper_min_burst_10s}")
        if elite_score < route_policy.sniper_min_elite:
            blockers.append(f"elite<{route_policy.sniper_min_elite}")
        if attention < route_policy.heating_min_attention:
            blockers.append(f"attention<{route_policy.heating_min_attention:.2f}")
        if len(confirmations) < route_policy.heating_min_confirmations:
            blockers.append(f"route_confirmations<{route_policy.heating_min_confirmations}")
        if not flow_confirmations:
            blockers.append("route_flow_confirmation_missing")
        if not quality_confirmations:
            blockers.append("route_quality_confirmation_missing")

    return {
        "tier": route_tier,
        "confirmations": confirmations,
        "blockers": blockers,
        "sniper_blockers": sniper_blockers,
        "sniper_ready": sniper_ready,
        "sniper_fast_track": sniper_fast_track,
        "route_confidence": route_confidence,
    }


def _heating_delivery_for_policy(route_decision: dict[str, Any], route_policy: RouteSignalPolicy) -> tuple[bool, list[str]]:
    tier = str(route_decision.get("tier") or "")
    confirmations = route_decision.get("confirmations") if isinstance(route_decision.get("confirmations"), list) else []
    blockers = route_decision.get("blockers") if isinstance(route_decision.get("blockers"), list) else []
    route_confidence = _safe_float(route_decision.get("route_confidence"))
    if tier == "sniper":
        return True, ["sniper_route", f"route_confidence:{route_confidence:.2f}", *confirmations]
    if tier == "heating_up" and confirmations and route_confidence >= route_policy.heating_delivery_min_confidence:
        return True, [f"route_confidence:{route_confidence:.2f}", *confirmations]
    return False, blockers or ["route_not_heating_ready"]


def evaluate_signal_parameter_set(record: dict[str, Any], params: dict[str, Any]) -> SignalSweepDecision:
    candidate_overrides, route_overrides, scoring_overrides, gate_overrides = _split_signal_overrides(params)
    candidate_policy = _policy_with_overrides(candidate_signal_policy(), candidate_overrides)
    route_policy = _policy_with_overrides(route_signal_policy(), route_overrides)
    scoring_policy = attention_scoring_policy()
    metrics, dex_summary, base_metrics = _snapshot_metrics(record)

    attention_score = _safe_float(record.get("attention_score"))
    risk_score = _safe_float(record.get("risk_score"), 1.0)
    creator_score = _safe_float(record.get("creator_score"))
    confidence_score = _safe_float(record.get("confidence_score"), attention_score)
    extra = {
        "metrics": base_metrics,
        "attention_metrics": metrics,
        "age_bypass_until": _safe_float(record.get("age_bypass_until")),
        "bonding_curve_present": bool(record.get("bonding_curve_present", True)),
        "bonding_curve_liquidity": record.get("bonding_curve_liquidity"),
    }
    gate_config = {
        "candidate_gate_attention_min": gate_overrides.get("candidate_gate_attention_min", record.get("candidate_gate_attention_min")),
        "candidate_gate_min_age_sec": gate_overrides.get("candidate_gate_min_age_sec", record.get("candidate_gate_min_age_sec")),
    }
    candidate_gate_pass, gate_reasons, _lifecycle = admission_check_candidate(
        attention_score=attention_score,
        risk_score=risk_score,
        extra=extra,
        dex_summary=dex_summary or None,
        attention_unavailable=False,
        gate_config=gate_config,
        token_is_tradeable=bool(record.get("token_is_tradeable", True)),
        bonding_curve_verified=bool(record.get("bonding_curve_verified", True)),
    )
    candidate_send_eligible, candidate_reasons, candidate_confirmations = _candidate_send_reasons_for_policy(
        attention_score=attention_score,
        creator_score=creator_score,
        metrics=metrics,
        dex_summary=dex_summary or None,
        policy=candidate_policy,
    )
    route_decision = _route_decision_for_policy(
        attention_score=attention_score,
        elite_score=_safe_int(record.get("elite_score")),
        unique_10s=_safe_int(record.get("unique_10s")),
        burst_10s=_safe_int(record.get("burst_10s")),
        hard_fail_from_authority_checks=bool(record.get("authority_hard_fail")),
        metrics=metrics,
        dex_summary=dex_summary or None,
        route_policy=route_policy,
        scoring_policy=scoring_policy,
        score_weights=scoring_overrides,
    )
    heating_allowed, heating_reasons = _heating_delivery_for_policy(route_decision, route_policy)
    score_value = max(confidence_score, _safe_float(route_decision.get("route_confidence")))
    score_pass = score_value >= _safe_float(scoring_overrides.get("min_confidence"), 0.0)

    predicted_route = "reject"
    route_tier = str(route_decision.get("tier") or "watch")
    if score_pass and route_tier == "sniper" and heating_allowed:
        predicted_route = "sniper"
    elif score_pass and route_tier == "heating_up" and heating_allowed:
        predicted_route = "heating_up"
    elif score_pass and candidate_gate_pass and candidate_send_eligible:
        predicted_route = "candidate"
    elif candidate_gate_pass or candidate_confirmations:
        predicted_route = "watch"

    combined_candidate_reasons = list(gate_reasons)
    for reason in candidate_reasons:
        if reason not in combined_candidate_reasons:
            combined_candidate_reasons.append(reason)
    return SignalSweepDecision(
        predicted_route=predicted_route,
        candidate_gate_pass=bool(candidate_gate_pass),
        candidate_send_eligible=bool(candidate_send_eligible),
        candidate_confirmations=list(candidate_confirmations),
        candidate_reasons=combined_candidate_reasons,
        route_tier=route_tier,
        route_confidence=round(_safe_float(route_decision.get("route_confidence")), 6),
        route_confirmations=list(route_decision.get("confirmations") or []),
        route_blockers=list(route_decision.get("blockers") or []),
        heating_allowed=bool(heating_allowed),
        heating_reasons=list(heating_reasons),
        score_pass=bool(score_pass),
        score_value=round(score_value, 6),
    )


def _record_positive(record: dict[str, Any]) -> bool:
    if "is_positive" in record:
        return bool(record.get("is_positive"))
    return _safe_float(record.get("pnl_pct"), _safe_float(record.get("pnl"))) > 0.0


def _record_quality(record: dict[str, Any]) -> float:
    if "quality_score" in record:
        return max(0.0, min(1.0, _safe_float(record.get("quality_score"))))
    pnl = _safe_float(record.get("pnl_pct"), _safe_float(record.get("pnl")))
    if pnl <= 0:
        return 0.0
    return max(0.0, min(1.0, pnl / 20.0))


def _route_precision(records: list[dict[str, Any]], route: str) -> float:
    subset = [item for item in records if str(item.get("_predicted_route")) == route]
    if not subset:
        return 0.0
    positives = sum(1 for item in subset if _record_positive(item))
    return round(positives / len(subset), 6)


def score_signal_sweep(
    records: list[dict[str, Any]],
    decisions: list[SignalSweepDecision],
) -> tuple[SignalSweepMetrics, dict[str, int]]:
    evaluated: list[dict[str, Any]] = []
    route_counts = {"candidate": 0, "heating_up": 0, "sniper": 0, "watch": 0, "reject": 0}
    labeled_total = 0
    labeled_matches = 0
    for record, decision in zip(records, decisions):
        route_counts[decision.predicted_route] = route_counts.get(decision.predicted_route, 0) + 1
        enriched = dict(record)
        enriched["_predicted_route"] = decision.predicted_route
        if decision.predicted_route in {"candidate", "heating_up", "sniper"}:
            evaluated.append(enriched)
        target_route = str(record.get("target_route") or "").strip().lower()
        if target_route:
            labeled_total += 1
            if target_route == decision.predicted_route:
                labeled_matches += 1

    base = score_selected_outcomes(evaluated, total_records=len(records))
    avg_quality = 0.0
    if evaluated:
        avg_quality = sum(_record_quality(item) for item in evaluated) / len(evaluated)
    route_accuracy = (labeled_matches / labeled_total) if labeled_total else 0.0
    candidate_precision = _route_precision(evaluated, "candidate")
    heating_precision = _route_precision(evaluated, "heating_up")
    sniper_precision = _route_precision(evaluated, "sniper")
    robustness = max(
        0.0,
        min(
            1.0,
            (
                base.stability * 0.35
                + route_accuracy * 0.25
                + (1.0 - base.false_positive_rate) * 0.25
                + avg_quality * 0.15
            ),
        ),
    )
    return (
        SignalSweepMetrics(
            selected=base.selected,
            total=base.total,
            expectancy=base.expectancy,
            win_rate=base.win_rate,
            precision=base.precision,
            false_positive_rate=base.false_positive_rate,
            max_drawdown=base.max_drawdown,
            stability=base.stability,
            avg_quality=round(avg_quality, 6),
            route_accuracy=round(route_accuracy, 6),
            candidate_precision=candidate_precision,
            heating_precision=heating_precision,
            sniper_precision=sniper_precision,
            robustness=round(robustness, 6),
            candidate_selected=route_counts.get("candidate", 0),
            heating_selected=route_counts.get("heating_up", 0),
            sniper_selected=route_counts.get("sniper", 0),
        ),
        route_counts,
    )


def rank_signal_sweep_results(results: list[SignalSweepResult]) -> list[SignalSweepResult]:
    return sorted(results, key=lambda item: item.rank_key, reverse=True)


def run_signal_parameter_sweep(
    *,
    records: list[dict[str, Any]],
    parameter_space: dict[str, list[Any]] | None = None,
    mode: str = "grid",
    sample_size: int | None = None,
    seed: int = 42,
) -> list[SignalSweepResult]:
    if mode not in {"grid", "random"}:
        raise ValueError("unsupported_search_mode")
    resolved_space = parameter_space or build_signal_parameter_space()
    if mode == "grid":
        parameter_sets = expand_parameter_space(resolved_space)
    else:
        parameter_sets = sample_parameter_space(
            resolved_space,
            sample_size=max(1, int(sample_size or 1)),
            seed=seed,
        )

    results: list[SignalSweepResult] = []
    for params in parameter_sets:
        decisions = [evaluate_signal_parameter_set(record, params) for record in records]
        metrics, route_counts = score_signal_sweep(records, decisions)
        rank_key = (
            metrics.robustness,
            metrics.avg_quality,
            metrics.precision,
            metrics.expectancy,
            -metrics.max_drawdown,
            metrics.sniper_precision,
            metrics.selected,
        )
        results.append(
            SignalSweepResult(
                params=dict(params),
                metrics=metrics,
                rank_key=rank_key,
                route_counts=dict(route_counts),
            )
        )
    return rank_signal_sweep_results(results)

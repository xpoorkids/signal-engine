from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import time
from typing import Any

from worker.config import (
    TRADE_VALIDATION_MAX_MARKET_DATA_AGE_SEC,
    TRADE_VALIDATION_MAX_BUY_SLIPPAGE_BPS,
    TRADE_VALIDATION_MAX_RISK,
    TRADE_VALIDATION_MAX_SELL_SLIPPAGE_BPS,
    TRADE_VALIDATION_MAX_TOP_HOLDER_PCT,
    TRADE_VALIDATION_MAX_WALLET_TOP_HOLDER_PCT,
    TRADE_VALIDATION_MIN_LIQ_USD,
    TRADE_VALIDATION_QUOTE_TTL_SEC,
    TRADE_VALIDATION_SIZE_USD,
    TRADE_VALIDATION_QUOTE_PROVIDER,
    TRADE_VALIDATION_REQUIRE_VENUE_QUOTES,
)


logger = logging.getLogger(__name__)
POLICY_NAME = "elite_pretrade_validator"
POLICY_VERSION = "v1"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


@dataclass(frozen=True)
class PairContext:
    token: str
    pair_address: str
    dex_id: str
    price_usd: float
    liquidity_usd: float
    token_reserve: float
    quote_reserve_usd: float
    quote_symbol: str


@dataclass(frozen=True)
class QuoteSimulation:
    side: str
    route_exists: bool
    amount_in: float
    amount_in_units: str
    expected_output_usd: float
    expected_output_tokens: float
    execution_price_usd: float
    slippage_bps: float
    pair_address: str
    dex_id: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    passed: bool
    observed: Any
    threshold: Any
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationResult:
    approved: bool
    token: str
    policy_name: str
    policy_version: str
    validated_ts: int
    quote_expires_ts: int
    intended_size_usd: float
    market_target: str
    pair_address: str | None
    dex_id: str | None
    reasons: list[str]
    warnings: list[str]
    checks: list[dict[str, Any]]
    market_data: dict[str, Any]
    buy_quote: dict[str, Any] | None
    sell_quote: dict[str, Any] | None
    risk_summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Backward-compatible alias while the integration migrates to the shorter name.
TradeValidationResult = ValidationResult


def _snapshot_ts(dex_summary: dict[str, Any] | None) -> int | None:
    if not isinstance(dex_summary, dict):
        return None
    try:
        value = dex_summary.get("snapshot_ts")
        if value in (None, ""):
            return None
        return int(float(value))
    except Exception:
        return None


def _market_data_age_seconds(dex_summary: dict[str, Any] | None, now_ts: int) -> float | None:
    snapshot_ts = _snapshot_ts(dex_summary)
    if snapshot_ts is None:
        return None
    return max(0.0, float(now_ts - snapshot_ts))


def _market_shape_value(dex_summary: dict[str, Any] | None, *names: str) -> float:
    payload = dex_summary if isinstance(dex_summary, dict) else {}
    for name in names:
        try:
            value = payload.get(name)
            if value not in (None, ""):
                return float(value)
        except Exception:
            continue
    return 0.0


def market_manipulation_reasons(dex_summary: dict[str, Any] | None) -> list[str]:
    liq = _market_shape_value(dex_summary, "liquidity_usd", "liquidity")
    vol5m = _market_shape_value(dex_summary, "volume_m5", "volume_m5_usd", "volume_5m")
    buys5m = int(_market_shape_value(dex_summary, "txns_m5_buys", "buys_5m"))
    sells5m = int(_market_shape_value(dex_summary, "txns_m5_sells", "sells_5m"))
    price_change_m5 = _market_shape_value(dex_summary, "price_change_m5", "price_change_5m")
    price_change_h1 = _market_shape_value(dex_summary, "price_change_h1", "price_change_1h")
    buy_sell_ratio = float(buys5m) / float(max(sells5m, 1)) if buys5m > 0 else 0.0
    sell_buy_ratio = float(sells5m) / float(max(buys5m, 1)) if buys5m > 0 else 0.0
    vol_liq_ratio = vol5m / liq if liq > 0.0 else 0.0
    reasons: list[str] = []

    if (price_change_m5 >= 35.0 or price_change_h1 >= 140.0) and not (
        buys5m >= 25 and buy_sell_ratio >= 1.8 and sell_buy_ratio <= 0.85
    ):
        reasons.append("price_pump_without_flow")
    if liq > 0.0 and vol_liq_ratio >= 3.0 and buys5m < 25:
        reasons.append("liquidity_volume_spike")
    if price_change_m5 >= 20.0 and buys5m < 12:
        reasons.append("one_sided_chart_risk")
    return reasons


def build_pair_context(best_pair: dict[str, Any] | None, token: str) -> PairContext | None:
    if not isinstance(best_pair, dict) or not token:
        return None
    base = best_pair.get("baseToken") if isinstance(best_pair.get("baseToken"), dict) else {}
    if str(base.get("address") or "") != token:
        return None
    price_usd = _to_float(best_pair.get("priceUsd"))
    liquidity_block = best_pair.get("liquidity") if isinstance(best_pair.get("liquidity"), dict) else {}
    liq_usd = _to_float(liquidity_block.get("usd"))
    pair_address = str(best_pair.get("pairAddress") or "").strip()
    dex_id = str(best_pair.get("dexId") or "").strip()
    quote = best_pair.get("quoteToken") if isinstance(best_pair.get("quoteToken"), dict) else {}
    quote_symbol = str(quote.get("symbol") or "QUOTE")
    if not pair_address or not dex_id or not price_usd or price_usd <= 0 or not liq_usd or liq_usd <= 0:
        return None
    token_reserve = liq_usd / (2.0 * price_usd)
    quote_reserve_usd = liq_usd / 2.0
    if token_reserve <= 0 or quote_reserve_usd <= 0:
        return None
    return PairContext(
        token=token,
        pair_address=pair_address,
        dex_id=dex_id,
        price_usd=price_usd,
        liquidity_usd=liq_usd,
        token_reserve=token_reserve,
        quote_reserve_usd=quote_reserve_usd,
        quote_symbol=quote_symbol,
    )


def simulate_buy_quote(ctx: PairContext | None, amount_in_usd: float) -> QuoteSimulation | None:
    if ctx is None or amount_in_usd <= 0:
        return None
    quote_after = ctx.quote_reserve_usd + amount_in_usd
    token_out = ctx.token_reserve - ((ctx.token_reserve * ctx.quote_reserve_usd) / quote_after)
    if token_out <= 0:
        return None
    execution_price = amount_in_usd / token_out
    slippage_bps = max(0.0, ((execution_price / ctx.price_usd) - 1.0) * 10000.0)
    return QuoteSimulation(
        side="buy",
        route_exists=True,
        amount_in=amount_in_usd,
        amount_in_units="usd",
        expected_output_usd=token_out * ctx.price_usd,
        expected_output_tokens=token_out,
        execution_price_usd=execution_price,
        slippage_bps=slippage_bps,
        pair_address=ctx.pair_address,
        dex_id=ctx.dex_id,
    )


def simulate_sell_quote(ctx: PairContext | None, token_amount: float) -> QuoteSimulation | None:
    if ctx is None or token_amount <= 0:
        return None
    token_after = ctx.token_reserve + token_amount
    quote_out = ctx.quote_reserve_usd - ((ctx.token_reserve * ctx.quote_reserve_usd) / token_after)
    if quote_out <= 0:
        return None
    execution_price = quote_out / token_amount
    slippage_bps = max(0.0, (1.0 - (execution_price / ctx.price_usd)) * 10000.0)
    return QuoteSimulation(
        side="sell",
        route_exists=True,
        amount_in=token_amount,
        amount_in_units="token",
        expected_output_usd=quote_out,
        expected_output_tokens=token_amount,
        execution_price_usd=execution_price,
        slippage_bps=slippage_bps,
        pair_address=ctx.pair_address,
        dex_id=ctx.dex_id,
    )


def validate_trade(
    *,
    token: str,
    best_pair: dict[str, Any] | None,
    dex_summary: dict[str, Any] | None,
    token_meta: dict[str, Any] | None,
    risk_score: float | None,
    wallet_risk: dict[str, Any] | None,
    mint_authority: bool | None,
    freeze_authority: bool | None,
    top_holder_ratio: float | None,
    intended_size_usd: float | None = None,
) -> dict[str, Any]:
    from worker.route_quote import resolve_buy_quote, resolve_sell_quote

    now_ts = int(time.time())
    size_usd = float(intended_size_usd or TRADE_VALIDATION_SIZE_USD)
    logger.info("[trade-validator-start] token=%s size_usd=%.2f has_dex=%s", token, size_usd, 1 if dex_summary else 0)
    reasons: list[str] = []
    warnings: list[str] = []
    checks: list[ValidationCheck] = []
    market_target = "dex" if dex_summary else "unknown"
    ctx = build_pair_context(best_pair, token)
    liq_usd = _to_float((dex_summary or {}).get("liquidity_usd"))
    market_data_age_sec = _market_data_age_seconds(dex_summary, now_ts)

    market_data_fresh = market_data_age_sec is not None and market_data_age_sec <= TRADE_VALIDATION_MAX_MARKET_DATA_AGE_SEC
    checks.append(
        ValidationCheck(
            "market_data_age_sec",
            market_data_fresh,
            None if market_data_age_sec is None else round(market_data_age_sec, 3),
            TRADE_VALIDATION_MAX_MARKET_DATA_AGE_SEC,
            None if market_data_fresh else "market_data_stale",
        )
    )
    if not market_data_fresh:
        reasons.append("market_data_stale")

    if ctx is None:
        reasons.extend(["buy_route_missing", "sell_route_missing"])
        checks.append(ValidationCheck("pair_context", False, None, "base_token_pair_with_liquidity", "route_unavailable"))
    else:
        checks.append(ValidationCheck("pair_context", True, ctx.pair_address, "base_token_pair_with_liquidity"))

    liq_pass = liq_usd is not None and liq_usd >= TRADE_VALIDATION_MIN_LIQ_USD
    checks.append(ValidationCheck("liquidity_usd", liq_pass, liq_usd, TRADE_VALIDATION_MIN_LIQ_USD, None if liq_pass else "liquidity_below_threshold"))
    if not liq_pass:
        reasons.append("liquidity_below_threshold")

    manipulation_reasons = market_manipulation_reasons(dex_summary)
    for reason in manipulation_reasons:
        checks.append(ValidationCheck(f"market_shape:{reason}", False, True, False, reason))
    reasons.extend(manipulation_reasons)

    if mint_authority is True:
        checks.append(ValidationCheck("mint_authority", False, True, False, "mint_authority_active"))
        reasons.append("mint_authority_active")
    elif mint_authority is None:
        warnings.append("mint_authority_unavailable")
    else:
        checks.append(ValidationCheck("mint_authority", True, False, False))

    if freeze_authority is True:
        checks.append(ValidationCheck("freeze_authority", False, True, False, "freeze_authority_active"))
        reasons.append("freeze_authority_active")
    elif freeze_authority is None:
        warnings.append("freeze_authority_unavailable")
    else:
        checks.append(ValidationCheck("freeze_authority", True, False, False))

    if risk_score is None:
        warnings.append("risk_score_unavailable")
    else:
        risk_pass = risk_score <= TRADE_VALIDATION_MAX_RISK
        checks.append(ValidationCheck("risk_score", risk_pass, risk_score, TRADE_VALIDATION_MAX_RISK, None if risk_pass else "risk_above_threshold"))
        if not risk_pass:
            reasons.append("risk_above_threshold")

    if top_holder_ratio is None:
        warnings.append("top_holder_ratio_unavailable")
    else:
        top_holder_pass = top_holder_ratio <= TRADE_VALIDATION_MAX_TOP_HOLDER_PCT
        checks.append(ValidationCheck("top_holder_ratio", top_holder_pass, top_holder_ratio, TRADE_VALIDATION_MAX_TOP_HOLDER_PCT, None if top_holder_pass else "top_holder_concentration"))
        if not top_holder_pass:
            reasons.append("top_holder_concentration")

    wallet_top_holder_pct = _to_float((wallet_risk or {}).get("top_holder_pct"))
    if wallet_top_holder_pct is None:
        warnings.append("wallet_top_holder_pct_unavailable")
    else:
        wallet_top_holder_pass = wallet_top_holder_pct <= TRADE_VALIDATION_MAX_WALLET_TOP_HOLDER_PCT
        checks.append(ValidationCheck("wallet_top_holder_pct", wallet_top_holder_pass, wallet_top_holder_pct, TRADE_VALIDATION_MAX_WALLET_TOP_HOLDER_PCT, None if wallet_top_holder_pass else "wallet_holder_concentration"))
        if not wallet_top_holder_pass:
            reasons.append("wallet_holder_concentration")

    reserve_buy_quote = simulate_buy_quote(ctx, size_usd)
    buy_quote_result = resolve_buy_quote(
        token=token,
        token_meta=token_meta,
        best_pair=best_pair,
        ctx=ctx,
        reserve_fallback_quote=reserve_buy_quote.as_dict() if reserve_buy_quote is not None else None,
        amount_in_usd=size_usd,
    )
    warnings.extend(buy_quote_result.warnings)
    buy_quote_payload = buy_quote_result.quote
    buy_provider = buy_quote_result.provider
    if buy_quote_payload is None:
        reasons.extend(buy_quote_result.errors or ["buy_quote_unavailable"])
        checks.append(ValidationCheck("buy_route_exists", False, False, True, "buy_quote_unavailable"))
        if TRADE_VALIDATION_REQUIRE_VENUE_QUOTES:
            checks.append(ValidationCheck("venue_quote_required_buy", False, buy_provider, "venue", "venue_quote_unavailable"))
        sell_quote_payload = None
    else:
        checks.append(ValidationCheck("buy_route_exists", True, True, True))
        buy_slippage_bps = round(float(buy_quote_payload.get("slippage_bps") or 0.0), 2)
        buy_pass = buy_slippage_bps <= TRADE_VALIDATION_MAX_BUY_SLIPPAGE_BPS
        checks.append(ValidationCheck("buy_slippage_bps", buy_pass, buy_slippage_bps, TRADE_VALIDATION_MAX_BUY_SLIPPAGE_BPS, None if buy_pass else "buy_slippage_too_high"))
        if not buy_pass:
            reasons.append("buy_slippage_too_high")
        if TRADE_VALIDATION_REQUIRE_VENUE_QUOTES:
            venue_buy_pass = buy_provider != "reserve"
            checks.append(ValidationCheck("venue_quote_buy", venue_buy_pass, buy_provider, "venue", None if venue_buy_pass else "venue_quote_unavailable"))
            if not venue_buy_pass:
                reasons.append("venue_quote_unavailable")
        sell_token_amount = float(buy_quote_payload.get("expected_output_tokens") or 0.0)
        reserve_sell_quote = simulate_sell_quote(ctx, sell_token_amount)
        sell_quote_result = resolve_sell_quote(
            token=token,
            token_meta=token_meta,
            best_pair=best_pair,
            ctx=ctx,
            reserve_fallback_quote=reserve_sell_quote.as_dict() if reserve_sell_quote is not None else None,
            token_amount=sell_token_amount,
        )
        warnings.extend(sell_quote_result.warnings)
        sell_quote_payload = sell_quote_result.quote
        sell_provider = sell_quote_result.provider
        if sell_quote_payload is None:
            reasons.extend(sell_quote_result.errors or ["sell_quote_unavailable"])
            checks.append(ValidationCheck("sell_route_exists", False, False, True, "sell_quote_unavailable"))
            if TRADE_VALIDATION_REQUIRE_VENUE_QUOTES:
                checks.append(ValidationCheck("venue_quote_sell", False, sell_provider, "venue", "venue_quote_unavailable"))
        else:
            checks.append(ValidationCheck("sell_route_exists", True, True, True))
            sell_slippage_bps = round(float(sell_quote_payload.get("slippage_bps") or 0.0), 2)
            sell_pass = sell_slippage_bps <= TRADE_VALIDATION_MAX_SELL_SLIPPAGE_BPS
            checks.append(ValidationCheck("sell_slippage_bps", sell_pass, sell_slippage_bps, TRADE_VALIDATION_MAX_SELL_SLIPPAGE_BPS, None if sell_pass else "sell_slippage_too_high"))
            if not sell_pass:
                reasons.append("sell_slippage_too_high")
            if TRADE_VALIDATION_REQUIRE_VENUE_QUOTES:
                venue_sell_pass = sell_provider != "reserve"
                checks.append(ValidationCheck("venue_quote_sell", venue_sell_pass, sell_provider, "venue", None if venue_sell_pass else "venue_quote_unavailable"))
                if not venue_sell_pass:
                    reasons.append("venue_quote_unavailable")

    result = ValidationResult(
        approved=len(reasons) == 0,
        token=token,
        policy_name=POLICY_NAME,
        policy_version=POLICY_VERSION,
        validated_ts=now_ts,
        quote_expires_ts=now_ts + max(1, int(TRADE_VALIDATION_QUOTE_TTL_SEC)),
        intended_size_usd=size_usd,
        market_target=market_target,
        pair_address=ctx.pair_address if ctx else None,
        dex_id=ctx.dex_id if ctx else None,
        reasons=sorted(set(reasons)),
        warnings=sorted(set(warnings)),
        checks=[item.as_dict() for item in checks],
        market_data={
            "snapshot_ts": _snapshot_ts(dex_summary),
            "age_sec": None if market_data_age_sec is None else round(market_data_age_sec, 3),
            "max_age_sec": TRADE_VALIDATION_MAX_MARKET_DATA_AGE_SEC,
            "quote_ttl_sec": max(1, int(TRADE_VALIDATION_QUOTE_TTL_SEC)),
            "quote_provider": TRADE_VALIDATION_QUOTE_PROVIDER,
            "require_venue_quotes": TRADE_VALIDATION_REQUIRE_VENUE_QUOTES,
        },
        buy_quote=buy_quote_payload,
        sell_quote=sell_quote_payload,
        risk_summary={
            "risk_score": risk_score,
            "top_holder_ratio": top_holder_ratio,
            "wallet_top_holder_pct": wallet_top_holder_pct,
            "wallet_risk": wallet_risk or {},
            "mint_authority": mint_authority,
            "freeze_authority": freeze_authority,
        },
    ).as_dict()
    logger.info(
        "[trade-validator-complete] token=%s approved=%s size_usd=%.2f pair=%s reasons=%s warnings=%s",
        token,
        1 if result["approved"] else 0,
        size_usd,
        result["pair_address"] or "",
        result["reasons"],
        result["warnings"],
    )
    if not result["approved"]:
        logger.warning("[trade-validator-rejected] token=%s reasons=%s", token, result["reasons"])
    return result

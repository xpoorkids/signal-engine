from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Any

from worker.config import (
    TRADE_VALIDATION_MAX_BUY_SLIPPAGE_BPS,
    TRADE_VALIDATION_MAX_RISK,
    TRADE_VALIDATION_MAX_SELL_SLIPPAGE_BPS,
    TRADE_VALIDATION_MAX_TOP_HOLDER_PCT,
    TRADE_VALIDATION_MAX_WALLET_TOP_HOLDER_PCT,
    TRADE_VALIDATION_MIN_LIQ_USD,
    TRADE_VALIDATION_SIZE_USD,
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
    intended_size_usd: float
    market_target: str
    pair_address: str | None
    dex_id: str | None
    reasons: list[str]
    warnings: list[str]
    checks: list[dict[str, Any]]
    buy_quote: dict[str, Any] | None
    sell_quote: dict[str, Any] | None
    risk_summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Backward-compatible alias while the integration migrates to the shorter name.
TradeValidationResult = ValidationResult


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
    risk_score: float | None,
    wallet_risk: dict[str, Any] | None,
    mint_authority: bool | None,
    freeze_authority: bool | None,
    top_holder_ratio: float | None,
    intended_size_usd: float | None = None,
) -> dict[str, Any]:
    size_usd = float(intended_size_usd or TRADE_VALIDATION_SIZE_USD)
    logger.info("[trade-validator-start] token=%s size_usd=%.2f has_dex=%s", token, size_usd, 1 if dex_summary else 0)
    reasons: list[str] = []
    warnings: list[str] = []
    checks: list[ValidationCheck] = []
    market_target = "dex" if dex_summary else "unknown"
    ctx = build_pair_context(best_pair, token)
    liq_usd = _to_float((dex_summary or {}).get("liquidity_usd"))

    if ctx is None:
        reasons.extend(["buy_route_missing", "sell_route_missing"])
        checks.append(ValidationCheck("pair_context", False, None, "base_token_pair_with_liquidity", "route_unavailable"))
    else:
        checks.append(ValidationCheck("pair_context", True, ctx.pair_address, "base_token_pair_with_liquidity"))

    liq_pass = liq_usd is not None and liq_usd >= TRADE_VALIDATION_MIN_LIQ_USD
    checks.append(ValidationCheck("liquidity_usd", liq_pass, liq_usd, TRADE_VALIDATION_MIN_LIQ_USD, None if liq_pass else "liquidity_below_threshold"))
    if not liq_pass:
        reasons.append("liquidity_below_threshold")

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

    buy_quote = simulate_buy_quote(ctx, size_usd)
    if buy_quote is None:
        reasons.append("buy_quote_unavailable")
        buy_quote_payload = None
        sell_quote_payload = None
    else:
        buy_pass = buy_quote.slippage_bps <= TRADE_VALIDATION_MAX_BUY_SLIPPAGE_BPS
        checks.append(ValidationCheck("buy_slippage_bps", buy_pass, round(buy_quote.slippage_bps, 2), TRADE_VALIDATION_MAX_BUY_SLIPPAGE_BPS, None if buy_pass else "buy_slippage_too_high"))
        if not buy_pass:
            reasons.append("buy_slippage_too_high")
        sell_quote = simulate_sell_quote(ctx, buy_quote.expected_output_tokens)
        if sell_quote is None:
            reasons.append("sell_quote_unavailable")
            sell_quote_payload = None
        else:
            sell_pass = sell_quote.slippage_bps <= TRADE_VALIDATION_MAX_SELL_SLIPPAGE_BPS
            checks.append(ValidationCheck("sell_slippage_bps", sell_pass, round(sell_quote.slippage_bps, 2), TRADE_VALIDATION_MAX_SELL_SLIPPAGE_BPS, None if sell_pass else "sell_slippage_too_high"))
            if not sell_pass:
                reasons.append("sell_slippage_too_high")
            sell_quote_payload = sell_quote.as_dict()
        buy_quote_payload = buy_quote.as_dict()

    result = ValidationResult(
        approved=len(reasons) == 0,
        token=token,
        policy_name=POLICY_NAME,
        policy_version=POLICY_VERSION,
        intended_size_usd=size_usd,
        market_target=market_target,
        pair_address=ctx.pair_address if ctx else None,
        dex_id=ctx.dex_id if ctx else None,
        reasons=sorted(set(reasons)),
        warnings=sorted(set(warnings)),
        checks=[item.as_dict() for item in checks],
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

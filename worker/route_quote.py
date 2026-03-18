from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Any

import requests

from worker.config import (
    TRADE_VALIDATION_QUOTE_PROVIDER,
    TRADE_VALIDATION_REQUIRE_VENUE_QUOTES,
    TRADE_VALIDATION_VENUE_SLIPPAGE_BPS,
    JUPITER_API_KEY,
    JUPITER_QUOTE_BASE,
)
from worker.metadata import fetch_token_metadata


logger = logging.getLogger(__name__)

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4JxjL9Q9m1bRnbZVdH1v"
COMMON_TOKEN_DECIMALS = {
    SOL_MINT: 9,
    USDC_MINT: 6,
    USDT_MINT: 6,
}


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _normalize_provider(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"reserve", "jupiter", "hybrid"}:
        return value
    return "hybrid"


@dataclass(frozen=True)
class RouteQuoteResult:
    provider: str
    side: str
    quote: dict[str, Any] | None
    warnings: list[str]
    errors: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _metadata_decimals(mint: str) -> int | None:
    if mint in COMMON_TOKEN_DECIMALS:
        return COMMON_TOKEN_DECIMALS[mint]
    meta = fetch_token_metadata(mint)
    if not isinstance(meta, dict):
        return None
    decimals = meta.get("decimals")
    try:
        if decimals is None:
            return None
        return int(decimals)
    except Exception:
        return None


def _price_native(best_pair: dict[str, Any] | None) -> float | None:
    if not isinstance(best_pair, dict):
        return None
    return _to_float(best_pair.get("priceNative"))


def _quote_mint(best_pair: dict[str, Any] | None) -> str | None:
    if not isinstance(best_pair, dict):
        return None
    quote = best_pair.get("quoteToken")
    if not isinstance(quote, dict):
        return None
    mint = str(quote.get("address") or "").strip()
    return mint or None


def _quote_symbol(best_pair: dict[str, Any] | None) -> str:
    if not isinstance(best_pair, dict):
        return "QUOTE"
    quote = best_pair.get("quoteToken")
    if not isinstance(quote, dict):
        return "QUOTE"
    return str(quote.get("symbol") or "QUOTE")


def _quote_usd_price(best_pair: dict[str, Any] | None, ctx: Any) -> float | None:
    if ctx is None:
        return None
    native = _price_native(best_pair)
    if native and native > 0:
        return ctx.price_usd / native
    quote_mint = _quote_mint(best_pair)
    if quote_mint in {USDC_MINT, USDT_MINT}:
        return 1.0
    return None


def _token_decimals(token: str, token_meta: dict[str, Any] | None) -> int | None:
    if isinstance(token_meta, dict):
        try:
            decimals = token_meta.get("decimals")
            if decimals is not None:
                return int(decimals)
        except Exception:
            pass
    return _metadata_decimals(token)


def _quote_input_units(best_pair: dict[str, Any] | None, ctx: Any, amount_in_usd: float) -> tuple[float | None, int | None, str | None]:
    quote_mint = _quote_mint(best_pair)
    if not quote_mint:
        return None, None, None
    quote_usd_price = _quote_usd_price(best_pair, ctx)
    if quote_usd_price is None or quote_usd_price <= 0:
        return None, None, quote_mint
    quote_decimals = _metadata_decimals(quote_mint)
    if quote_decimals is None:
        return None, None, quote_mint
    quote_amount = amount_in_usd / quote_usd_price
    return quote_amount, quote_decimals, quote_mint


def _build_jupiter_url(params: dict[str, Any]) -> str:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{JUPITER_QUOTE_BASE}?{query}"


def _jupiter_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if JUPITER_API_KEY:
        headers["x-api-key"] = JUPITER_API_KEY
    return headers


def _quote_via_jupiter(*, input_mint: str, output_mint: str, amount: int, side: str, ctx: Any, amount_in_units: str, amount_in_display: float, quote_usd_price: float | None = None, token_decimals: int | None = None, output_decimals: int | None = None) -> RouteQuoteResult:
    if not JUPITER_API_KEY:
        return RouteQuoteResult(provider="jupiter", side=side, quote=None, warnings=["jupiter_api_key_missing"], errors=["venue_quote_unavailable"])
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": int(TRADE_VALIDATION_VENUE_SLIPPAGE_BPS),
        "restrictIntermediateTokens": "true",
    }
    try:
        logger.info("[route-quote-request] provider=jupiter side=%s input=%s output=%s amount=%s", side, input_mint, output_mint, amount)
        response = requests.get(_build_jupiter_url(params), headers=_jupiter_headers(), timeout=8)
        if response.status_code >= 300:
            logger.warning("[route-quote-failed] provider=jupiter side=%s status=%s", side, response.status_code)
            return RouteQuoteResult(provider="jupiter", side=side, quote=None, warnings=[], errors=["venue_quote_unavailable"])
        data = response.json()
        route_plan = data.get("routePlan") if isinstance(data.get("routePlan"), list) else []
        out_amount_raw = int(str(data.get("outAmount") or "0"))
        if out_amount_raw <= 0 or not route_plan:
            return RouteQuoteResult(provider="jupiter", side=side, quote=None, warnings=[], errors=["venue_quote_unavailable"])
        labels = sorted(
            {
                str(((item.get("swapInfo") or {}) if isinstance(item, dict) else {}).get("label") or "").strip()
                for item in route_plan
                if str(((item.get("swapInfo") or {}) if isinstance(item, dict) else {}).get("label") or "").strip()
            }
        )
        if side == "buy":
            if token_decimals is None or token_decimals < 0:
                return RouteQuoteResult(provider="jupiter", side=side, quote=None, warnings=[], errors=["token_decimals_unavailable"])
            expected_output_tokens = out_amount_raw / float(10**token_decimals)
            expected_output_usd = expected_output_tokens * ctx.price_usd
            execution_price_usd = amount_in_display / expected_output_tokens if expected_output_tokens > 0 else 0.0
            slippage_bps = max(0.0, ((execution_price_usd / ctx.price_usd) - 1.0) * 10000.0) if expected_output_tokens > 0 else 0.0
        else:
            if quote_usd_price is None or quote_usd_price <= 0 or output_decimals is None or output_decimals < 0:
                return RouteQuoteResult(provider="jupiter", side=side, quote=None, warnings=[], errors=["quote_token_price_unavailable"])
            expected_output_quote = out_amount_raw / float(10**output_decimals)
            expected_output_usd = expected_output_quote * quote_usd_price
            expected_output_tokens = amount_in_display
            execution_price_usd = expected_output_usd / amount_in_display if amount_in_display > 0 else 0.0
            slippage_bps = max(0.0, (1.0 - (execution_price_usd / ctx.price_usd)) * 10000.0) if amount_in_display > 0 else 0.0
        quote = {
            "side": side,
            "route_exists": True,
            "amount_in": amount_in_display,
            "amount_in_units": amount_in_units,
            "expected_output_usd": expected_output_usd,
            "expected_output_tokens": expected_output_tokens,
            "execution_price_usd": execution_price_usd,
            "slippage_bps": slippage_bps,
            "pair_address": ctx.pair_address,
            "dex_id": ctx.dex_id,
            "reason": None,
        }
        quote["provider"] = "jupiter"
        quote["quote_context_slot"] = data.get("contextSlot")
        quote["quote_time_taken"] = data.get("timeTaken")
        quote["price_impact_pct"] = _to_float(data.get("priceImpactPct"))
        quote["route_labels"] = labels
        return RouteQuoteResult(provider="jupiter", side=side, quote=quote, warnings=[], errors=[])
    except Exception:
        logger.exception("[route-quote-error] provider=jupiter side=%s", side)
        return RouteQuoteResult(provider="jupiter", side=side, quote=None, warnings=[], errors=["venue_quote_unavailable"])


def resolve_buy_quote(*, token: str, token_meta: dict[str, Any] | None, best_pair: dict[str, Any] | None, ctx: Any, reserve_fallback_quote: dict[str, Any] | None, amount_in_usd: float) -> RouteQuoteResult:
    provider = _normalize_provider(TRADE_VALIDATION_QUOTE_PROVIDER)
    if provider == "reserve":
        return RouteQuoteResult(provider="reserve", side="buy", quote=reserve_fallback_quote, warnings=[], errors=[] if reserve_fallback_quote else ["buy_quote_unavailable"])
    quote_amount, quote_decimals, quote_mint = _quote_input_units(best_pair, ctx, amount_in_usd)
    token_decimals = _token_decimals(token, token_meta)
    if ctx and quote_amount and quote_decimals is not None and quote_mint:
        raw_amount = int(round(quote_amount * (10**quote_decimals)))
        venue_result = _quote_via_jupiter(
            input_mint=quote_mint,
            output_mint=token,
            amount=raw_amount,
            side="buy",
            ctx=ctx,
            amount_in_units="usd",
            amount_in_display=amount_in_usd,
            token_decimals=token_decimals,
        )
        if venue_result.quote:
            return venue_result
        if provider == "jupiter" or TRADE_VALIDATION_REQUIRE_VENUE_QUOTES:
            return venue_result
        warnings = list(venue_result.warnings) + list(venue_result.errors)
        return RouteQuoteResult(provider="reserve", side="buy", quote=reserve_fallback_quote, warnings=warnings, errors=[] if reserve_fallback_quote else ["buy_quote_unavailable"])
    if provider == "jupiter" or TRADE_VALIDATION_REQUIRE_VENUE_QUOTES:
        return RouteQuoteResult(provider="jupiter", side="buy", quote=None, warnings=[], errors=["venue_quote_unavailable"])
    return RouteQuoteResult(provider="reserve", side="buy", quote=reserve_fallback_quote, warnings=["venue_quote_context_incomplete"], errors=[] if reserve_fallback_quote else ["buy_quote_unavailable"])


def resolve_sell_quote(*, token: str, token_meta: dict[str, Any] | None, best_pair: dict[str, Any] | None, ctx: Any, reserve_fallback_quote: dict[str, Any] | None, token_amount: float) -> RouteQuoteResult:
    provider = _normalize_provider(TRADE_VALIDATION_QUOTE_PROVIDER)
    if provider == "reserve":
        return RouteQuoteResult(provider="reserve", side="sell", quote=reserve_fallback_quote, warnings=[], errors=[] if reserve_fallback_quote else ["sell_quote_unavailable"])
    token_decimals = _token_decimals(token, token_meta)
    quote_mint = _quote_mint(best_pair)
    quote_decimals = _metadata_decimals(quote_mint or "")
    quote_usd_price = _quote_usd_price(best_pair, ctx)
    if ctx and token_decimals is not None and token_decimals >= 0 and quote_mint and quote_decimals is not None and quote_usd_price:
        raw_amount = int(round(token_amount * (10**token_decimals)))
        venue_result = _quote_via_jupiter(
            input_mint=token,
            output_mint=quote_mint,
            amount=raw_amount,
            side="sell",
            ctx=ctx,
            amount_in_units="token",
            amount_in_display=token_amount,
            quote_usd_price=quote_usd_price,
            output_decimals=quote_decimals,
        )
        if venue_result.quote:
            return venue_result
        if provider == "jupiter" or TRADE_VALIDATION_REQUIRE_VENUE_QUOTES:
            return venue_result
        warnings = list(venue_result.warnings) + list(venue_result.errors)
        return RouteQuoteResult(provider="reserve", side="sell", quote=reserve_fallback_quote, warnings=warnings, errors=[] if reserve_fallback_quote else ["sell_quote_unavailable"])
    if provider == "jupiter" or TRADE_VALIDATION_REQUIRE_VENUE_QUOTES:
        return RouteQuoteResult(provider="jupiter", side="sell", quote=None, warnings=[], errors=["venue_quote_unavailable"])
    return RouteQuoteResult(provider="reserve", side="sell", quote=reserve_fallback_quote, warnings=["venue_quote_context_incomplete"], errors=[] if reserve_fallback_quote else ["sell_quote_unavailable"])

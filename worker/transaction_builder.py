from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except Exception:
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _extract_check_threshold(checks: list[dict[str, Any]], name: str) -> float | int | None:
    for item in checks:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") != name:
            continue
        threshold = item.get("threshold")
        if isinstance(threshold, (int, float)):
            return threshold
        numeric = _to_float(threshold)
        if numeric is not None:
            return int(numeric) if float(numeric).is_integer() else numeric
    return None


@dataclass(frozen=True)
class ExecutionRequest:
    signal_id: str | None
    source_event_type: str
    source: str
    token: str
    side: str
    intended_size_usd: float
    market_target: str
    pair_address: str | None
    dex_id: str | None
    validated_ts: int | None
    quote_expires_ts: int | None
    quote_ttl_sec: int | None
    buy_quote: dict[str, Any]
    sell_quote: dict[str, Any]
    market_data: dict[str, Any]
    checks: list[dict[str, Any]]
    risk_summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransactionRouteMetadata:
    market_target: str
    pair_address: str | None
    dex_id: str | None
    buy_provider: str | None
    sell_provider: str | None
    buy_route_exists: bool
    sell_route_exists: bool
    route_labels: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransactionAmounts:
    intended_size_usd: float
    amount_in: float
    amount_in_units: str
    expected_output_tokens: float
    expected_output_usd: float
    quoted_execution_price_usd: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransactionQuoteMetadata:
    validated_ts: int | None
    quote_expires_ts: int | None
    quote_ttl_sec: int | None
    market_data_snapshot_ts: int | None
    market_data_age_sec: float | None
    quote_provider_mode: str | None
    require_venue_quotes: bool
    buy_context_slot: int | None
    sell_context_slot: int | None
    buy_time_taken: float | None
    sell_time_taken: float | None
    buy_price_impact_pct: float | None
    sell_price_impact_pct: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransactionSlippageControls:
    expected_buy_slippage_bps: float
    expected_sell_slippage_bps: float
    max_buy_slippage_bps: float | int | None
    max_sell_slippage_bps: float | int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransactionExecutionConstraints:
    max_market_data_age_sec: float | int | None
    require_fresh_quotes: bool
    require_sell_route: bool
    no_signing: bool
    no_broadcast: bool
    execution_mode_target: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransactionIntent:
    intent_id: str
    intent_type: str
    signal_id: str | None
    token: str
    side: str
    route: TransactionRouteMetadata
    amounts: TransactionAmounts
    quote: TransactionQuoteMetadata
    slippage: TransactionSlippageControls
    constraints: TransactionExecutionConstraints

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "intent_type": self.intent_type,
            "signal_id": self.signal_id,
            "token": self.token,
            "side": self.side,
            "route": self.route.as_dict(),
            "amounts": self.amounts.as_dict(),
            "quote": self.quote.as_dict(),
            "slippage": self.slippage.as_dict(),
            "constraints": self.constraints.as_dict(),
        }


def build_execution_request(*, event: Any, validation: dict[str, Any]) -> ExecutionRequest:
    if not isinstance(validation, dict) or not validation.get("approved"):
        raise ValueError("validation_not_approved")

    token = str(getattr(event, "token", "") or "").strip()
    if not token:
        raise ValueError("token_missing")

    intended_size_usd = _to_float(validation.get("intended_size_usd"))
    if intended_size_usd is None or intended_size_usd <= 0:
        raise ValueError("intended_size_invalid")

    buy_quote = validation.get("buy_quote") if isinstance(validation.get("buy_quote"), dict) else {}
    sell_quote = validation.get("sell_quote") if isinstance(validation.get("sell_quote"), dict) else {}
    if not buy_quote:
        raise ValueError("buy_quote_missing")
    if not sell_quote:
        raise ValueError("sell_quote_missing")

    validated_ts = _to_int(validation.get("validated_ts"))
    quote_expires_ts = _to_int(validation.get("quote_expires_ts"))
    quote_ttl_sec = None
    if validated_ts is not None and quote_expires_ts is not None:
        quote_ttl_sec = max(0, quote_expires_ts - validated_ts)

    market_data = validation.get("market_data") if isinstance(validation.get("market_data"), dict) else {}
    checks = validation.get("checks") if isinstance(validation.get("checks"), list) else []
    risk_summary = validation.get("risk_summary") if isinstance(validation.get("risk_summary"), dict) else {}
    extra = getattr(event, "extra", {}) if isinstance(getattr(event, "extra", {}), dict) else {}
    signal_id = str(extra.get("_signal_id") or "").strip() or None

    return ExecutionRequest(
        signal_id=signal_id,
        source_event_type=str(getattr(event, "type", "") or ""),
        source=str(getattr(event, "source", "") or ""),
        token=token,
        side="buy",
        intended_size_usd=float(intended_size_usd),
        market_target=str(validation.get("market_target") or "dex"),
        pair_address=str(validation.get("pair_address") or "").strip() or None,
        dex_id=str(validation.get("dex_id") or "").strip() or None,
        validated_ts=validated_ts,
        quote_expires_ts=quote_expires_ts,
        quote_ttl_sec=quote_ttl_sec,
        buy_quote=buy_quote,
        sell_quote=sell_quote,
        market_data=market_data,
        checks=[item for item in checks if isinstance(item, dict)],
        risk_summary=risk_summary,
    )


def build_transaction_intent(request: ExecutionRequest) -> TransactionIntent:
    if request.side != "buy":
        raise ValueError("unsupported_side")

    amount_in = _to_float(request.buy_quote.get("amount_in"))
    if amount_in is None or amount_in <= 0:
        amount_in = request.intended_size_usd
    amount_in_units = str(request.buy_quote.get("amount_in_units") or "usd")
    expected_output_tokens = _to_float(request.buy_quote.get("expected_output_tokens"))
    expected_output_usd = _to_float(request.buy_quote.get("expected_output_usd"))
    quoted_execution_price_usd = _to_float(request.buy_quote.get("execution_price_usd"))
    expected_sell_slippage_bps = _to_float(request.sell_quote.get("slippage_bps"))
    expected_buy_slippage_bps = _to_float(request.buy_quote.get("slippage_bps"))

    if expected_output_tokens is None or expected_output_tokens <= 0:
        raise ValueError("expected_output_tokens_invalid")
    if expected_output_usd is None or expected_output_usd <= 0:
        expected_output_usd = request.intended_size_usd
    if quoted_execution_price_usd is None or quoted_execution_price_usd <= 0:
        raise ValueError("quoted_execution_price_invalid")
    if expected_buy_slippage_bps is None:
        raise ValueError("expected_buy_slippage_missing")
    if expected_sell_slippage_bps is None:
        raise ValueError("expected_sell_slippage_missing")

    route_labels = sorted(
        {
            str(label).strip()
            for label in list(request.buy_quote.get("route_labels") or []) + list(request.sell_quote.get("route_labels") or [])
            if str(label).strip()
        }
    )

    route = TransactionRouteMetadata(
        market_target=request.market_target,
        pair_address=request.pair_address,
        dex_id=request.dex_id,
        buy_provider=str(request.buy_quote.get("provider") or request.market_data.get("quote_provider") or "").strip() or None,
        sell_provider=str(request.sell_quote.get("provider") or request.market_data.get("quote_provider") or "").strip() or None,
        buy_route_exists=bool(request.buy_quote.get("route_exists", True)),
        sell_route_exists=bool(request.sell_quote.get("route_exists", True)),
        route_labels=route_labels,
    )
    amounts = TransactionAmounts(
        intended_size_usd=request.intended_size_usd,
        amount_in=float(amount_in),
        amount_in_units=amount_in_units,
        expected_output_tokens=float(expected_output_tokens),
        expected_output_usd=float(expected_output_usd),
        quoted_execution_price_usd=float(quoted_execution_price_usd),
    )
    quote = TransactionQuoteMetadata(
        validated_ts=request.validated_ts,
        quote_expires_ts=request.quote_expires_ts,
        quote_ttl_sec=request.quote_ttl_sec,
        market_data_snapshot_ts=_to_int(request.market_data.get("snapshot_ts")),
        market_data_age_sec=_to_float(request.market_data.get("age_sec")),
        quote_provider_mode=str(request.market_data.get("quote_provider") or "").strip() or None,
        require_venue_quotes=bool(request.market_data.get("require_venue_quotes")),
        buy_context_slot=_to_int(request.buy_quote.get("quote_context_slot")),
        sell_context_slot=_to_int(request.sell_quote.get("quote_context_slot")),
        buy_time_taken=_to_float(request.buy_quote.get("quote_time_taken")),
        sell_time_taken=_to_float(request.sell_quote.get("quote_time_taken")),
        buy_price_impact_pct=_to_float(request.buy_quote.get("price_impact_pct")),
        sell_price_impact_pct=_to_float(request.sell_quote.get("price_impact_pct")),
    )
    slippage = TransactionSlippageControls(
        expected_buy_slippage_bps=float(expected_buy_slippage_bps),
        expected_sell_slippage_bps=float(expected_sell_slippage_bps),
        max_buy_slippage_bps=_extract_check_threshold(request.checks, "buy_slippage_bps"),
        max_sell_slippage_bps=_extract_check_threshold(request.checks, "sell_slippage_bps"),
    )
    constraints = TransactionExecutionConstraints(
        max_market_data_age_sec=_extract_check_threshold(request.checks, "market_data_age_sec"),
        require_fresh_quotes=True,
        require_sell_route=True,
        no_signing=True,
        no_broadcast=True,
        execution_mode_target="validate_only|shadow|live",
    )

    intent_payload = {
        "request": request.as_dict(),
        "route": route.as_dict(),
        "amounts": amounts.as_dict(),
        "quote": quote.as_dict(),
        "slippage": slippage.as_dict(),
        "constraints": constraints.as_dict(),
    }
    intent_id = hashlib.sha256(_json_dumps(intent_payload).encode("utf-8")).hexdigest()[:24]
    intent = TransactionIntent(
        intent_id=intent_id,
        intent_type="validator_approved_transaction_intent",
        signal_id=request.signal_id,
        token=request.token,
        side=request.side,
        route=route,
        amounts=amounts,
        quote=quote,
        slippage=slippage,
        constraints=constraints,
    )
    logger.info(
        "[tx-builder] token=%s signal_id=%s intent_id=%s pair=%s buy_provider=%s sell_provider=%s expiry=%s",
        request.token,
        request.signal_id or "",
        intent.intent_id,
        route.pair_address or "",
        route.buy_provider or "",
        route.sell_provider or "",
        quote.quote_expires_ts or 0,
    )
    return intent

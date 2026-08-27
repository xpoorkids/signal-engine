from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.services.action_engine_service import ActionEngineService
from app.services.manual_position_service import ManualPositionService


router = APIRouter()


class BuyFillRequest(BaseModel):
    token: str | None = None
    symbol: str | None = None
    token_quantity: float
    gross_usd: float
    gross_sol: float = 0.0
    fees_usd: float = 0.0
    execution_price_usd: float | None = None
    risk_profile: str = "aggressive"
    exit_style: str = "catalyst_runner"
    catalyst_mode: bool = False
    original_thesis: str | None = None
    invalidation_conditions: list[str] | None = None
    tx_signature: str | None = None
    fill_ts: int | None = None
    source: str = "manual"
    notes: str | None = None


class SellFillRequest(BaseModel):
    token_quantity: float | None = None
    full: bool = False
    gross_usd: float
    gross_sol: float = 0.0
    net_amount_usd: float | None = None
    execution_price_usd: float | None = None
    fees_usd: float = 0.0
    slippage_pct: float | None = None
    price_impact_pct: float | None = None
    tx_signature: str | None = None
    fill_ts: int | None = None
    source: str = "manual"
    notes: str | None = None


class CatalystRequest(BaseModel):
    token: str
    title: str
    catalyst_type: str = "unknown"
    verification_status: str = "unverified"
    description: str | None = None
    original_source: str | None = None
    secondary_confirmations: list[str] | None = None
    first_observed_ts: int | None = None
    expected_start_ts: int | None = None
    expected_end_ts: int | None = None
    catalyst_confidence_pct: float = 0.0
    catalyst_flow_confirmation: bool = False
    market_reaction_start_price_usd: float | None = None


def _positions() -> ManualPositionService:
    service = ManualPositionService()
    service.init_schema()
    return service


def _actions() -> ActionEngineService:
    service = ActionEngineService()
    service.init_schema()
    return service


def _payload(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


@router.post("/positions/manual/buy")
def mark_bought(payload: BuyFillRequest):
    try:
        return _positions().mark_bought(**_payload(payload))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/positions/{position_id}/buy")
def record_buy(position_id: str, payload: BuyFillRequest):
    data = _payload(payload)
    data.pop("token", None)
    data.pop("symbol", None)
    data.pop("risk_profile", None)
    data.pop("exit_style", None)
    data.pop("catalyst_mode", None)
    data.pop("original_thesis", None)
    data.pop("invalidation_conditions", None)
    try:
        return _positions().record_buy(position_id, **data)
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/positions/{position_id}/sell")
def record_sell(position_id: str, payload: SellFillRequest):
    try:
        return _positions().record_sell(position_id, **_payload(payload))
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/positions/{position_id}/close")
def close_position(position_id: str):
    try:
        return _positions().close_position(position_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")


@router.post("/positions/{position_id}/reopen")
def reopen_position(position_id: str):
    try:
        return _positions().reopen_position(position_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")


@router.patch("/positions/{position_id}")
def update_position(position_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return _positions().update_position(position_id, **payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/positions/{position_id}/history")
def position_history(position_id: str):
    try:
        return _positions().position_history(position_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")


@router.get("/positions/{position_id}/recommendation")
def position_recommendation(position_id: str):
    try:
        return _actions().recommend_for_position(position_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")


@router.post("/positions/{position_id}/recommendation")
def position_recommendation_post(position_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return _actions().recommend_for_position(
            position_id,
            market=payload.get("market") if isinstance(payload.get("market"), dict) else {},
            catalyst=payload.get("catalyst") if isinstance(payload.get("catalyst"), dict) else None,
            intended_size_usd=payload.get("intended_size_usd"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")


@router.post("/actions/recommendation")
def token_recommendation(payload: dict[str, Any] = Body(default={})):
    token = str(payload.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token_required")
    return _actions().recommend_for_token(
        token,
        market=payload.get("market") if isinstance(payload.get("market"), dict) else {},
        assessment=payload.get("assessment") if isinstance(payload.get("assessment"), dict) else {},
        catalyst=payload.get("catalyst") if isinstance(payload.get("catalyst"), dict) else None,
        intended_size_usd=payload.get("intended_size_usd"),
    )


@router.post("/catalysts")
def create_catalyst(payload: CatalystRequest):
    try:
        return _positions().create_catalyst(**_payload(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/catalysts/{catalyst_id}")
def update_catalyst(catalyst_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return _positions().update_catalyst(catalyst_id, **payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="catalyst_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/catalysts/{catalyst_id}/invalid")
def mark_catalyst_invalid(catalyst_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return _positions().mark_catalyst_invalid(catalyst_id, reason=str(payload.get("reason") or "operator_invalidated"))
    except KeyError:
        raise HTTPException(status_code=404, detail="catalyst_not_found")


@router.post("/positions/{position_id}/catalyst/{catalyst_id}")
def attach_catalyst(position_id: str, catalyst_id: str):
    try:
        return _positions().attach_catalyst(position_id, catalyst_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="position_not_found")

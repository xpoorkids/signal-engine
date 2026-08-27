from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.services.action_engine_service import ActionEngineService
from app.services.manual_position_service import ManualPositionService
from app.services.x_identity_service import XIdentityService


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


class XBlockedIdentityRequest(BaseModel):
    identity_id: str | None = None
    current_handle: str | None = None
    stable_x_user_id: str | None = None
    identity_confidence: str = "operator_supplied"
    operator_block_reason: str = "operator_blocked"
    notes: str | None = None


class XAliasRequest(BaseModel):
    handle: str
    first_observed_ts: int | None = None
    last_observed_ts: int | None = None
    source: str = "operator_manual"
    evidence_ts: int | None = None
    evidence: dict[str, Any] | None = None


class XStableIdRequest(BaseModel):
    stable_x_user_id: str


class XTokenLinkRequest(BaseModel):
    token: str
    link_type: str
    source: str = "operator_manual"
    identity_id: str | None = None
    stable_x_user_id: str | None = None
    handle: str | None = None
    profile_url: str | None = None
    evidence_ts: int | None = None
    identity_confidence: str = "unresolved"
    match_method: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class XObservationRequest(BaseModel):
    identity_id: str | None = None
    token: str | None = None
    evidence_type: str = "operator_screenshot"
    observed_current_handle: str | None = None
    observed_aliases: list[str] | None = None
    observed_rename_intervals: list[dict[str, Any]] | None = None
    evidence_ts: int | None = None
    source: str = "operator_manual"
    operator_notes: str | None = None
    stable_x_user_id_status: str = "unresolved"
    metadata: dict[str, Any] | None = None


def _positions() -> ManualPositionService:
    service = ManualPositionService()
    service.init_schema()
    return service


def _actions() -> ActionEngineService:
    service = ActionEngineService()
    service.init_schema()
    return service


def _x_identities() -> XIdentityService:
    service = XIdentityService()
    service.init_schema()
    service.initialize_seed_blocklist()
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


@router.post("/x-identities/seed")
def seed_x_identity_blocklist():
    return _x_identities().initialize_seed_blocklist()


@router.get("/x-identities/blocked")
def list_x_identity_blocks(include_disabled: bool = False):
    return {"identities": _x_identities().list_blocked_identities(include_disabled=include_disabled)}


@router.post("/x-identities/blocked")
def add_x_identity_block(payload: XBlockedIdentityRequest):
    try:
        return _x_identities().add_blocked_identity(**_payload(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/x-identities/{identity_id}/stable-id")
def add_x_identity_stable_id(identity_id: str, payload: XStableIdRequest):
    try:
        return _x_identities().add_stable_x_user_id(identity_id, payload.stable_x_user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/x-identities/{identity_id}/current-handle")
def add_x_identity_current_handle(identity_id: str, payload: XAliasRequest):
    try:
        return _x_identities().add_current_handle(identity_id, payload.handle, source=payload.source, evidence_ts=payload.evidence_ts)
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/x-identities/{identity_id}/aliases")
def add_x_identity_alias(identity_id: str, payload: XAliasRequest):
    try:
        return _x_identities().add_historical_alias(identity_id, **_payload(payload))
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/x-identities/{identity_id}/disable")
def disable_x_identity_block(identity_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return _x_identities().disable_block(identity_id, notes=str(payload.get("notes") or "") or None)
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")


@router.post("/x-identities/{identity_id}/restore")
def restore_x_identity_block(identity_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return _x_identities().restore_block(identity_id, notes=str(payload.get("notes") or "") or None)
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")


@router.post("/x-identities/token-links")
def link_x_identity_to_token(payload: XTokenLinkRequest):
    try:
        return _x_identities().link_token_identity(**_payload(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/x-identities/{identity_id}/tokens")
def x_identity_tokens(identity_id: str):
    return {"identity_id": identity_id, "token_links": _x_identities().list_token_links_for_identity(identity_id)}


@router.get("/x-identities/{identity_id}/history")
def x_identity_history(identity_id: str):
    service = _x_identities()
    identity = service.get_identity(identity_id)
    if not identity:
        raise HTTPException(status_code=404, detail="x_identity_not_found")
    return {"identity": identity, "risk_summary": service.risk_summary(identity_id), "token_links": service.list_token_links_for_identity(identity_id)}


@router.post("/x-identities/observations")
def add_x_identity_observation(payload: XObservationRequest):
    return _x_identities().add_observation(**_payload(payload))

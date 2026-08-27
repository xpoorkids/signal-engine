from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.services.operator_auth_service import require_operator_auth, require_x_identity_read_auth
from app.services.x_identity_service import StableXUserIdConflict, XIdentityService


router = APIRouter()


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


def _x_identities() -> XIdentityService:
    service = XIdentityService()
    service.init_schema()
    return service


def _payload(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _audit_auth_failure(action: str, detail: str) -> None:
    try:
        XIdentityService().audit_event(action, actor_type="anonymous", reason=detail, success=False, error_type=detail)
    except Exception:
        pass


OperatorAuth = Annotated[dict[str, str], Depends(require_operator_auth)]
ReadAuth = Annotated[dict[str, str], Depends(require_x_identity_read_auth)]


@router.post("/x-identities/seed")
def seed_x_identity_blocklist(auth: OperatorAuth, payload: dict[str, Any] = Body(default={})):
    force_restore = bool(payload.get("force_restore", False))
    return _x_identities().initialize_seed_blocklist(
        force_restore=force_restore,
        actor_fingerprint=auth.get("actor_fingerprint") or None,
        request_id=auth.get("request_id") or None,
    )


@router.get("/x-identities/blocked")
def list_x_identity_blocks(_auth: ReadAuth, include_disabled: bool = False):
    return {"identities": _x_identities().list_blocked_identities(include_disabled=include_disabled)}


@router.post("/x-identities/blocked")
def add_x_identity_block(payload: XBlockedIdentityRequest, auth: OperatorAuth):
    try:
        return _x_identities().add_blocked_identity(**_payload(payload), actor_fingerprint=auth.get("actor_fingerprint") or None, request_id=auth.get("request_id") or None)
    except StableXUserIdConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/x-identities/{identity_id}/stable-id")
def add_x_identity_stable_id(identity_id: str, payload: XStableIdRequest, auth: OperatorAuth):
    try:
        return _x_identities().add_stable_x_user_id(identity_id, payload.stable_x_user_id, actor_fingerprint=auth.get("actor_fingerprint") or None, request_id=auth.get("request_id") or None)
    except StableXUserIdConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/x-identities/{identity_id}/current-handle")
def add_x_identity_current_handle(identity_id: str, payload: XAliasRequest, auth: OperatorAuth):
    try:
        return _x_identities().add_current_handle(identity_id, payload.handle, source=payload.source, evidence_ts=payload.evidence_ts, actor_fingerprint=auth.get("actor_fingerprint") or None, request_id=auth.get("request_id") or None)
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/x-identities/{identity_id}/aliases")
def add_x_identity_alias(identity_id: str, payload: XAliasRequest, auth: OperatorAuth):
    try:
        return _x_identities().add_historical_alias(identity_id, **_payload(payload), actor_fingerprint=auth.get("actor_fingerprint") or None, request_id=auth.get("request_id") or None)
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/x-identities/{identity_id}/disable")
def disable_x_identity_block(identity_id: str, auth: OperatorAuth, payload: dict[str, Any] = Body(default={})):
    try:
        return _x_identities().disable_block(identity_id, notes=str(payload.get("notes") or "") or None, actor_fingerprint=auth.get("actor_fingerprint") or None, request_id=auth.get("request_id") or None)
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")


@router.post("/x-identities/{identity_id}/restore")
def restore_x_identity_block(identity_id: str, auth: OperatorAuth, payload: dict[str, Any] = Body(default={})):
    try:
        return _x_identities().restore_block(identity_id, notes=str(payload.get("notes") or "") or None, actor_fingerprint=auth.get("actor_fingerprint") or None, request_id=auth.get("request_id") or None)
    except KeyError:
        raise HTTPException(status_code=404, detail="x_identity_not_found")


@router.post("/x-identities/token-links")
def link_x_identity_to_token(payload: XTokenLinkRequest, auth: OperatorAuth):
    try:
        return _x_identities().link_token_identity(**_payload(payload), actor_fingerprint=auth.get("actor_fingerprint") or None, request_id=auth.get("request_id") or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/x-identities/{identity_id}/tokens")
def x_identity_tokens(identity_id: str, _auth: ReadAuth):
    return {"identity_id": identity_id, "token_links": _x_identities().list_token_links_for_identity(identity_id)}


@router.get("/x-identities/{identity_id}/history")
def x_identity_history(identity_id: str, _auth: ReadAuth):
    service = _x_identities()
    identity = service.get_identity(identity_id)
    if not identity:
        raise HTTPException(status_code=404, detail="x_identity_not_found")
    return {"identity": identity, "risk_summary": service.risk_summary(identity_id), "token_links": service.list_token_links_for_identity(identity_id)}


@router.post("/x-identities/observations")
def add_x_identity_observation(payload: XObservationRequest, auth: OperatorAuth):
    return _x_identities().add_observation(**_payload(payload), actor_fingerprint=auth.get("actor_fingerprint") or None, request_id=auth.get("request_id") or None)

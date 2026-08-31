from __future__ import annotations

import hashlib
import hmac
import os
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer


_OPERATOR_BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="SignalEngineOperatorBearer",
    description="Bearer token for operator-only Signal Engine actions.",
)
_INTERNAL_WRITE_HEADER = APIKeyHeader(
    name="X-Signal-Engine-Token",
    auto_error=False,
    scheme_name="SignalEngineInternalWriteToken",
    description="Internal worker-to-engine write token.",
)


def _enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}


def x_identity_management_enabled() -> bool:
    return _enabled("SIGNAL_ENGINE_X_IDENTITY_MANAGEMENT_ENABLED", "0")


def x_identity_read_public() -> bool:
    return _enabled("SIGNAL_ENGINE_X_IDENTITY_READ_PUBLIC", "0")


def operator_token_configured() -> bool:
    return bool(os.getenv("SIGNAL_ENGINE_OPERATOR_API_TOKEN", "").strip())


def operator_fingerprint(token: str) -> str:
    salt = "signal-engine-operator-api-token-v1"
    return hashlib.sha256(f"{salt}:{token}".encode("utf-8")).hexdigest()


def _audit_auth_rejection(request: Request, reason: str) -> None:
    try:
        from app.services.x_identity_service import XIdentityService

        XIdentityService().audit_event(
            "rejected_unauthorized_mutation",
            actor_type="anonymous",
            request_id=request.headers.get("X-Request-ID", ""),
            reason=reason,
            before={},
            after={"path": request.url.path},
            success=False,
            error_type=reason,
        )
    except Exception:
        pass


def _validate_operator_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> dict[str, str]:
    configured = os.getenv("SIGNAL_ENGINE_OPERATOR_API_TOKEN", "").strip()
    if not configured:
        _audit_auth_rejection(request, "operator_auth_not_configured")
        raise HTTPException(status_code=503, detail="operator_auth_not_configured")
    supplied = credentials.credentials.strip() if credentials and credentials.scheme.lower() == "bearer" else ""
    if not supplied or not hmac.compare_digest(supplied, configured):
        _audit_auth_rejection(request, "operator_auth_required")
        raise HTTPException(status_code=401, detail="operator_auth_required")
    return {
        "actor_type": "operator",
        "actor_fingerprint": operator_fingerprint(supplied),
        "request_id": request.headers.get("X-Request-ID", ""),
    }


def require_operator_api_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_OPERATOR_BEARER)] = None,
) -> dict[str, str]:
    return _validate_operator_token(request, credentials)


def require_internal_write_auth(
    token: Annotated[str | None, Security(_INTERNAL_WRITE_HEADER)] = None,
) -> dict[str, str]:
    configured = os.getenv("SIGNAL_ENGINE_INTERNAL_WRITE_TOKEN", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="internal_write_auth_not_configured")
    supplied = str(token or "").strip()
    if not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=403, detail="forbidden")
    return {
        "actor_type": "internal_service",
        "actor_fingerprint": operator_fingerprint(supplied),
        "request_id": "",
    }


def require_operator_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_OPERATOR_BEARER)] = None,
) -> dict[str, str]:
    if not x_identity_management_enabled():
        _audit_auth_rejection(request, "management_endpoint_disabled")
        raise HTTPException(status_code=404, detail="management_endpoint_disabled")
    return _validate_operator_token(request, credentials)


def require_x_identity_read_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_OPERATOR_BEARER)] = None,
) -> dict[str, str]:
    if x_identity_read_public():
        return {"actor_type": "public_read", "actor_fingerprint": "", "request_id": request.headers.get("X-Request-ID", "")}
    return require_operator_auth(request, credentials)

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any, Dict

class ScanRequestError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)

def _verify_signature(raw_body: bytes, signature: str | None, secret: str) -> None:
    if not secret or not signature:
        raise ScanRequestError(401, "Unauthorized")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ScanRequestError(401, "Unauthorized")

def _parse_json(raw_body: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise ScanRequestError(400, "Malformed JSON")
    if not isinstance(data, dict):
        raise ScanRequestError(422, "Invalid payload: expected JSON object")
    return data

def _validate_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        raise ScanRequestError(422, "Invalid payload: timestamp must be ISO 8601")

def _validate_payload(data: Dict[str, Any]) -> None:
    timestamp = data.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ScanRequestError(422, "Invalid payload: missing or invalid timestamp")
    _validate_timestamp(timestamp)

def _build_response(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "ok",
        "count": 1,
        "candidates": [
            {
                "symbol": "TEST",
                "chain": "sol",
                "reason": "stub_signal",
                "observed_at": data.get("timestamp"),
                "metrics": {
                    "social_velocity": 0,
                    "volume_delta": 0,
                    "liquidity_usd": 0,
                },
            }
        ],
    }

def handle_scan_request(
    raw_body: bytes,
    signature: str | None,
    secret: str,
) -> Dict[str, Any]:
    _verify_signature(raw_body, signature, secret)
    data = _parse_json(raw_body)
    _validate_payload(data)
    return _build_response(data)

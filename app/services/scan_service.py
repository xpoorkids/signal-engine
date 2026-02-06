from datetime import datetime, timezone
import hashlib
import hmac
import json
from datetime import datetime as dt_datetime

from app.services.dex_service import fetch_solana_pairs
from app.services.score_service import score_pairs


def process_scan():
    pairs = fetch_solana_pairs()
    scored = score_pairs(pairs)
    ts = datetime.now(timezone.utc).isoformat()
    for s in scored:
        s["observed_at"] = ts
    return scored


class ScanRequestError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


def _verify_signature(raw_body: bytes, signature: str | None, secret: str) -> None:
    if not secret or not signature:
        raise ScanRequestError(401, "Unauthorized")

    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise ScanRequestError(401, "Unauthorized")


def _parse_json(raw_body: bytes) -> dict:
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise ScanRequestError(400, "Malformed JSON")

    if not isinstance(data, dict):
        raise ScanRequestError(422, "Invalid payload: expected JSON object")

    return data


def _validate_timestamp(value: str) -> None:
    try:
        dt_datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        raise ScanRequestError(422, "Invalid payload: timestamp must be ISO 8601")


def _validate_payload(data: dict) -> None:
    ts = data.get("timestamp")
    if not isinstance(ts, str) or not ts.strip():
        raise ScanRequestError(422, "Invalid payload: missing or invalid timestamp")
    _validate_timestamp(ts)


def process_scan_payload(
    raw_body: bytes,
    signature: str | None,
    secret: str,
) -> dict:
    _verify_signature(raw_body, signature, secret)
    data = _parse_json(raw_body)
    _validate_payload(data)

    candidates = process_scan()
    return {
        "status": "ok",
        "count": len(candidates),
        "candidates": candidates,
    }

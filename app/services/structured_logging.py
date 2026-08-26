import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "bearer",
    "webhook",
    "secret",
    "password",
    "private_key",
    "seed_phrase",
    "access_token",
    "refresh_token",
)
_PRIVATE_WALLET_KEY_PARTS = (
    "private_wallet",
    "operator_wallet",
    "wallet_private",
)
_WEBHOOK_RE = re.compile(r"https://(?:[^/\s]+/)*api/webhooks/[^\s\"']+", re.IGNORECASE)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-~+/]+=*", re.IGNORECASE)


def _normalize_tag(tag: str) -> str:
    value = str(tag or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    return value or "log"


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS + _PRIVATE_WALLET_KEY_PARTS)


def redact_log_value(value: Any, *, key: Any = "") -> Any:
    if _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {nested_key: redact_log_value(nested_value, key=nested_key) for nested_key, nested_value in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_log_value(item) for item in value]
    if isinstance(value, str):
        redacted = _WEBHOOK_RE.sub(REDACTED, value)
        redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", redacted)
        return redacted
    return value


def _serialize_log_value(value: Any) -> str:
    value = redact_log_value(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, Enum):
        return _serialize_log_value(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return json.dumps(list(value), separators=(",", ":"), default=str)
    text = str(value)
    if text and all(ch.isalnum() or ch in "._:/-@" for ch in text):
        return text
    return json.dumps(text, separators=(",", ":"), default=str)


def structured_log_message(tag: str, **fields: Any) -> str:
    prefix = f"[{_normalize_tag(tag)}]"
    parts = [prefix]
    for key, value in fields.items():
        if not key:
            continue
        parts.append(f"{key}={_serialize_log_value(redact_log_value(value, key=key))}")
    return " ".join(parts)


def log_event(logger: logging.Logger, level: int, tag: str, **fields: Any) -> None:
    logger.log(level, structured_log_message(tag, **fields))

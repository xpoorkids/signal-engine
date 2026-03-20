import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def _normalize_tag(tag: str) -> str:
    value = str(tag or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    return value or "log"


def _serialize_log_value(value: Any) -> str:
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
        parts.append(f"{key}={_serialize_log_value(value)}")
    return " ".join(parts)


def log_event(logger: logging.Logger, level: int, tag: str, **fields: Any) -> None:
    logger.log(level, structured_log_message(tag, **fields))

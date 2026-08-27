from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from research.config import ResearchConfig
from research.storage import ResearchStore


PARSER_VERSION = "research-raw-cache-v1"


def cache_key(source: str, endpoint: str, params: dict[str, Any]) -> str:
    clean = json.dumps({"source": source, "endpoint": endpoint, "params": params}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def response_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def store_raw_response(
    config: ResearchConfig,
    *,
    source: str,
    endpoint: str,
    params: dict[str, Any],
    payload: dict[str, Any] | list[Any],
    status: str,
    completeness_status: str,
    token_id: str | None = None,
    requested_start_ts: int | None = None,
    requested_end_ts: int | None = None,
) -> dict[str, Any]:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    key = cache_key(source, endpoint, params)
    digest = response_hash(raw)
    fetched = int(time.time())
    raw_dir = config.data_dir / "raw" / source
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{key[:20]}-{digest[:20]}-{fetched}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    store = ResearchStore(config)
    store.init_schema()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO research_raw_fetches (
                fetch_id, source, endpoint, request_json, token_id, requested_start_ts,
                requested_end_ts, fetched_ts, status, response_hash, parser_version,
                completeness_status, cache_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"{key}:{digest}:{fetched}", source, endpoint, json.dumps(params, sort_keys=True), token_id, requested_start_ts, requested_end_ts, fetched, status, digest, PARSER_VERSION, completeness_status, key),
        )
    return {"cache_key": key, "response_hash": digest, "path": str(path), "fetched_ts": fetched}

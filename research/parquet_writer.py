from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from research.config import ResearchConfig
from research.storage import ResearchStore


def write_parquet_table(config: ResearchConfig, table_name: str, rows: list[dict[str, Any]], *, chain: str = "solana", token: str | None = None, data_mode: str = "source") -> dict[str, Any]:
    if not rows:
        return {"table": table_name, "row_count": 0, "path": None, "data_mode": data_mode}
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as exc:
        return {"table": table_name, "row_count": 0, "path": None, "data_mode": data_mode, "status": "parquet_dependency_unavailable", "error_type": type(exc).__name__}

    prefix = (token or "unknown")[:2] or "na"
    observed = rows[0].get("observed_at") or int(time.time())
    try:
        t = time.gmtime(int(observed))
    except Exception:
        t = time.gmtime()
    out_dir = config.data_dir / "parquet" / table_name / f"chain={chain}" / f"token_prefix={prefix}" / f"year={t.tm_year}" / f"month={t.tm_mon:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    deduped = _dedupe_rows(rows)
    row_key = ",".join(str(row.get("row_id")) for row in deduped)
    file_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{table_name}:{token}:{data_mode}:{row_key}").hex
    final = out_dir / f"{file_id}.parquet"
    tmp = out_dir / f".{file_id}.tmp"
    table = pa.Table.from_pylist([_json_safe(row) for row in deduped])
    pq.write_table(table, tmp)
    os.replace(tmp, final)
    store = ResearchStore(config)
    store.init_schema()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO research_parquet_files
            (file_id, table_name, path, row_count, chain, token, data_mode, schema_json, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, table_name, str(final), table.num_rows, chain, token, data_mode, json.dumps(table.schema.names, sort_keys=True), int(time.time())),
        )
    return {"table": table_name, "row_count": table.num_rows, "path": str(final), "data_mode": data_mode}


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    return {key: json.dumps(value, sort_keys=True, default=str) if isinstance(value, (dict, list)) else value for key, value in row.items()}


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        row_id = str(row.get("row_id") or json.dumps(row, sort_keys=True, default=str))
        if row_id in seen:
            continue
        seen.add(row_id)
        out.append(row)
    return out

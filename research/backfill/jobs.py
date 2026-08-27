from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

from research.config import ResearchConfig
from research.models import JOB_STATUSES
from research.raw_cache import store_raw_response
from research.storage import ResearchStore


@dataclass(frozen=True)
class BackfillResult:
    completed: int
    partial: int
    failed: int
    source_unavailable: int
    next_step: str


def create_or_resume_job(
    config: ResearchConfig,
    *,
    cohort: str,
    token_id: str,
    source: str,
    stage: str,
    requested_start_ts: int | None = None,
    requested_end_ts: int | None = None,
) -> str:
    store = ResearchStore(config)
    store.init_schema()
    job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{cohort}:{token_id}:{source}:{stage}:{requested_start_ts}:{requested_end_ts}").hex
    now = int(time.time())
    with store.connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO research_jobs (
                job_id, cohort, token_id, source, stage, requested_start_ts,
                requested_end_ts, status, updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (job_id, cohort, token_id, source, stage, requested_start_ts, requested_end_ts, now),
        )
    return job_id


def update_job(config: ResearchConfig, job_id: str, *, status: str, **fields: Any) -> None:
    if status not in JOB_STATUSES:
        raise ValueError("invalid_job_status")
    allowed = {
        "completed_start_ts",
        "completed_end_ts",
        "next_cursor",
        "attempt_count",
        "last_error",
        "rate_limit_reset_ts",
        "records_written",
        "raw_response_hashes_json",
    }
    sql = ["status=?", "updated_ts=?"]
    values: list[Any] = [status, int(time.time())]
    for key, value in fields.items():
        if key in allowed:
            sql.append(f"{key}=?")
            values.append(json.dumps(value) if key.endswith("_json") and not isinstance(value, str) else value)
    values.append(job_id)
    store = ResearchStore(config)
    with store.connect() as conn:
        conn.execute(f"UPDATE research_jobs SET {', '.join(sql)} WHERE job_id=?", values)


def run_fixture_backfill(config: ResearchConfig, *, cohort: str = "operator_seed_cohort_v1", limit: int | None = None, dry_run: bool = False) -> BackfillResult:
    """Create provenance-backed placeholder jobs when real sources are unavailable.

    This does not fabricate historical features. It records source-unavailable
    jobs and immutable raw capability evidence so the pipeline can resume once
    real API access is configured.
    """
    store = ResearchStore(config)
    store.init_schema()
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT token_id FROM research_tokens WHERE source_label='operator_supplied' ORDER BY token_id LIMIT ?",
            (limit or 1000,),
        ).fetchall()
    completed = partial = failed = unavailable = 0
    for row in rows:
        token_id = row["token_id"]
        job_id = create_or_resume_job(config, cohort=cohort, token_id=token_id, source="capability_probe", stage="identity_backfill")
        if dry_run:
            partial += 1
            continue
        raw = store_raw_response(
            config,
            source="capability_probe",
            endpoint="offline_no_paid_source",
            params={"token_id": token_id, "cohort": cohort},
            payload={"token_id": token_id, "status": "source_unavailable", "missing_is_not_zero": True},
            status="source_unavailable",
            completeness_status="unavailable",
            token_id=token_id,
        )
        update_job(
            config,
            job_id,
            status="source_unavailable",
            records_written=0,
            raw_response_hashes_json=[raw["response_hash"]],
            last_error="historical source API not configured",
        )
        unavailable += 1
    return BackfillResult(completed, partial, failed, unavailable, "configure source APIs or run build-features with fixture pilot")


def bounded_retry_delays(max_attempts: int, *, base: float = 0.5, seed: int = 1337) -> list[float]:
    rng = random.Random(seed)
    return [min(60.0, base * (2 ** i)) + rng.uniform(0, base) for i in range(max(0, max_attempts))]


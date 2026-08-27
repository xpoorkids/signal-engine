from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from research.config import ResearchConfig
from research.models import SourceResult


SENSITIVE_QUERY_KEYS = {"api_key", "api-key", "apikey", "key", "token", "authorization", "auth", "access_token"}


class RequestBudgetExceeded(RuntimeError):
    pass


@dataclass
class RequestBudget:
    remaining: int

    def consume(self) -> None:
        if self.remaining <= 0:
            raise RequestBudgetExceeded("research_request_budget_exceeded")
        self.remaining -= 1


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, "REDACTED" if key.lower() in SENSITIVE_QUERY_KEYS else value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def request_hash(method: str, url: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> str:
    clean = {
        "method": method.upper(),
        "url": redact_url(url),
        "params": params or {},
        "json": json_body or {},
    }
    return hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class ResearchHttpClient:
    def __init__(self, config: ResearchConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        timeout = httpx.Timeout(
            connect=config.http_timeout_seconds,
            read=config.http_timeout_seconds,
            write=config.http_timeout_seconds,
            pool=config.http_timeout_seconds,
        )
        self.config = config
        self.budget = RequestBudget(config.request_budget)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "signal-engine-research-corpus/1.0"},
            follow_redirects=False,
        )
        self._global = asyncio.Semaphore(config.max_concurrency)
        self._source_semaphores: dict[str, asyncio.Semaphore] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    def _source_sem(self, source: str) -> asyncio.Semaphore:
        if source not in self._source_semaphores:
            self._source_semaphores[source] = asyncio.Semaphore(max(1, min(self.config.max_concurrency, 2)))
        return self._source_semaphores[source]

    async def request_json(
        self,
        *,
        source: str,
        operation: str,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        evidence_quality: str = "direct",
        requested_start_ts: int | None = None,
        requested_end_ts: int | None = None,
    ) -> SourceResult:
        correlation_id = uuid.uuid4().hex
        req_hash = request_hash(method, url, params=params, json_body=json_body)
        safe_url = redact_url(url)
        retry_count = 0
        errors: list[str] = []
        warnings: list[str] = []
        async with self._global, self._source_sem(source):
            for attempt in range(self.config.max_retries + 1):
                self.budget.consume()
                started = time.perf_counter()
                try:
                    response = await self._client.request(method, url, params=params, json=json_body, headers=headers)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    rate_limit = {
                        "retry_after": response.headers.get("Retry-After"),
                        "remaining": response.headers.get("x-ratelimit-remaining"),
                        "limit": response.headers.get("x-ratelimit-limit"),
                    }
                    if response.status_code in {401, 403}:
                        return SourceResult(source, operation, "unauthorized" if response.status_code == 401 else "plan_restricted", requested_start_ts, requested_end_ts, completeness="unavailable", evidence_quality="unavailable", fetched_at=int(time.time()), request_hash=req_hash, response_hash=hashlib.sha256(response.content).hexdigest(), retry_count=retry_count, rate_limit=rate_limit, warnings=warnings, errors=[f"http_{response.status_code}:{safe_url}"])
                    if response.status_code == 429 or 500 <= response.status_code <= 599:
                        if attempt < self.config.max_retries:
                            retry_count += 1
                            retry_after = response.headers.get("Retry-After")
                            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30.0, 0.5 * (2 ** attempt)) + random.Random(correlation_id + str(attempt)).uniform(0, 0.25)
                            await asyncio.sleep(delay)
                            continue
                        status = "rate_limited" if response.status_code == 429 else "source_unavailable"
                        return SourceResult(source, operation, status, requested_start_ts, requested_end_ts, completeness="unavailable", evidence_quality="unavailable", fetched_at=int(time.time()), request_hash=req_hash, response_hash=hashlib.sha256(response.content).hexdigest(), retry_count=retry_count, rate_limit=rate_limit, warnings=warnings, errors=[f"http_{response.status_code}:{safe_url}"])
                    if 400 <= response.status_code <= 499:
                        return SourceResult(source, operation, "invalid_request", requested_start_ts, requested_end_ts, completeness="unavailable", evidence_quality="unavailable", fetched_at=int(time.time()), request_hash=req_hash, response_hash=hashlib.sha256(response.content).hexdigest(), retry_count=retry_count, rate_limit=rate_limit, warnings=warnings, errors=[f"http_{response.status_code}:{safe_url}"])
                    try:
                        payload = response.json()
                    except ValueError:
                        return SourceResult(source, operation, "malformed_response", requested_start_ts, requested_end_ts, completeness="unavailable", evidence_quality="unavailable", fetched_at=int(time.time()), request_hash=req_hash, response_hash=hashlib.sha256(response.content).hexdigest(), retry_count=retry_count, rate_limit=rate_limit, warnings=warnings, errors=["json_decode_failed"])
                    records = payload if isinstance(payload, list) else [payload]
                    return SourceResult(
                        source=source,
                        operation=operation,
                        status="empty" if not records else "success",
                        requested_start_ts=requested_start_ts,
                        requested_end_ts=requested_end_ts,
                        records=records,
                        completeness="complete",
                        retention_status="source_reported_or_unprobed",
                        evidence_quality=evidence_quality,
                        fetched_at=int(time.time()),
                        request_hash=req_hash,
                        response_hash=hashlib.sha256(response.content).hexdigest(),
                        retry_count=retry_count,
                        rate_limit={**rate_limit, "elapsed_ms": elapsed_ms, "correlation_id": correlation_id},
                        warnings=warnings,
                        errors=errors,
                    )
                except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                    errors.append(f"{type(exc).__name__}:{safe_url}")
                    if attempt < self.config.max_retries:
                        retry_count += 1
                        await asyncio.sleep(min(30.0, 0.5 * (2 ** attempt)) + random.Random(correlation_id + str(attempt)).uniform(0, 0.25))
                        continue
                    return SourceResult(source, operation, "source_unavailable", requested_start_ts, requested_end_ts, completeness="unavailable", evidence_quality="unavailable", fetched_at=int(time.time()), request_hash=req_hash, retry_count=retry_count, warnings=warnings, errors=errors)
        return SourceResult(source, operation, "failed", requested_start_ts, requested_end_ts, completeness="unavailable", evidence_quality="unavailable", fetched_at=int(time.time()), request_hash=req_hash, retry_count=retry_count, warnings=warnings, errors=errors)

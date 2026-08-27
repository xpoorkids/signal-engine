from __future__ import annotations

import asyncio

import httpx
import pytest

from research.config import load_config
from research.http_client import RequestBudgetExceeded, ResearchHttpClient, redact_url


def test_redacts_sensitive_url_values() -> None:
    redacted = redact_url("https://example.test/path?api-key=secret&x=1&token=abc")
    assert "secret" not in redacted
    assert "abc" not in redacted
    assert "x=1" in redacted


def test_http_client_retries_rate_limit_with_retry_after(tmp_path) -> None:
    calls = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate"})
        return httpx.Response(200, json={"ok": True})

    config = load_config(db_path=str(tmp_path / "r.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    client = ResearchHttpClient(config, transport=httpx.MockTransport(handler))
    try:
        result = asyncio.run(client.request_json(source="test", operation="op", method="GET", url="https://example.test"))
    finally:
        asyncio.run(client.aclose())
    assert result.status == "success"
    assert result.retry_count == 1
    assert calls["count"] == 2


def test_http_client_request_budget(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    config = load_config(db_path=str(tmp_path / "r.db"), data_dir=str(tmp_path / "data"), artifact_dir=str(tmp_path / "artifacts"))
    config = config.__class__(**{**config.__dict__, "request_budget": 0})
    client = ResearchHttpClient(config, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RequestBudgetExceeded):
            asyncio.run(client.request_json(source="test", operation="op", method="GET", url="https://example.test"))
    finally:
        asyncio.run(client.aclose())


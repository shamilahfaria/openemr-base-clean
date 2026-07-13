"""Load-test harness — request shaping and metric aggregation.

Verifies the /chat scenario issues authenticated POSTs with a fresh session per
request (single-turn, cost-aware) and that non-2xx turns count as errors — all
against a mock transport, never a real server or the model.
"""
from __future__ import annotations

import json

import httpx
import pytest

from loadtest.run import chat_headers, percentile, scenario


def test_chat_headers_shape():
    headers = chat_headers("tok-1", "nurse-maria")
    assert headers["Authorization"] == "Bearer tok-1"
    assert headers["X-Clinician-Id"] == "nurse-maria"
    assert headers["Content-Type"] == "application/json"


def test_percentile_orders_and_handles_empty():
    assert percentile([10, 20, 30, 40], 50) == 30
    assert percentile([], 95) == 0.0


@pytest.mark.anyio
async def test_chat_scenario_issues_authenticated_posts_with_fresh_sessions():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "method": request.method,
                "url": str(request.url),
                "auth": request.headers.get("Authorization"),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(200, json={"degraded": False, "correlation_id": "c"})

    chat_ctx = {
        "patient": "pat-uuid",
        "message": "code status?",
        "headers": chat_headers("tok-1", "nurse-maria"),
    }
    result = await scenario(
        "http://test", "/chat", concurrency=2, total=4,
        chat_ctx=chat_ctx, error_status=400, transport=httpx.MockTransport(handler),
    )

    assert result["requests"] == 4
    assert result["error_rate_pct"] == 0.0
    assert all(r["method"] == "POST" for r in seen)
    assert all(r["url"].endswith("/chat") for r in seen)
    assert all(r["auth"] == "Bearer tok-1" for r in seen)
    assert all(r["body"]["patient_id"] == "pat-uuid" for r in seen)
    # A fresh session per request keeps each turn single-turn (bounded cost).
    assert len({r["body"]["session_id"] for r in seen}) == len(seen)


@pytest.mark.anyio
async def test_chat_scenario_counts_non_2xx_as_errors():
    chat_ctx = {"patient": "p", "message": "m", "headers": chat_headers("bad", "n")}
    result = await scenario(
        "http://test", "/chat", concurrency=1, total=3,
        chat_ctx=chat_ctx, error_status=400,
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    assert result["error_rate_pct"] == 100.0


@pytest.mark.anyio
async def test_health_scenario_uses_get():
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, text="ok")

    result = await scenario(
        "http://test", "/health", concurrency=2, total=4,
        transport=httpx.MockTransport(handler),
    )
    assert result["requests"] == 4
    assert all(method == "GET" for method in methods)

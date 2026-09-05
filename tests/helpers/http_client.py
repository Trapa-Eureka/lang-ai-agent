# pyright: reportUnknownMemberType=false
# httpx's streaming helpers and Starlette's lifespan_context are typed
# loosely enough to trip this check on ordinary usage. Scoped to this file.
"""In-process HTTP + SSE test client for the FastAPI app (docs/TESTING.md §4,
"API·SSE (httpx ASGI)") — shared by the API component tests and the e2e
scenarios, so both talk to the app exactly the way a real client would,
minus the network.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

TEST_TOKEN = "test-token"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@asynccontextmanager
async def api_client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """An httpx client bound to `app` over ASGI, with the app's lifespan
    entered — httpx's ASGITransport doesn't run lifespan on its own, and
    the app's graph only exists once it has (exactly as under uvicorn).
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def request_sse(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] = AUTH_HEADERS,
) -> tuple[int, list[dict[str, Any]]]:
    """Consume an SSE response into `(status, [{"event": ..., "data": {...}}, ...])`.

    `events` is empty unless the status is 200 — for tests where a request
    may legitimately be refused (a lost approval race, for instance).
    """
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    async with client.stream(method, url, json=body, headers=headers) as response:
        if response.status_code != 200:
            return response.status_code, []
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current["event"] = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                current["data"] = json.loads(line.removeprefix("data:").strip())
            elif not line and current:
                events.append(current)
                current = {}
    return 200, events


async def read_sse(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] = AUTH_HEADERS,
) -> list[dict[str, Any]]:
    """Consume an SSE response into [{"event": ..., "data": {...}}, ...]; the
    request must succeed."""
    status, events = await request_sse(client, method, url, body, headers=headers)
    assert status == 200, f"{method} {url} -> HTTP {status}"
    return events


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [event["event"] for event in events]


def token_text(events: list[dict[str, Any]]) -> str:
    return "".join(event["data"]["content"] for event in events if event["event"] == "token")

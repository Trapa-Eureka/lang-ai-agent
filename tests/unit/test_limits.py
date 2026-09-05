"""api/limits.py — the body-size ASGI middleware, called directly so every
branch (oversized, within limit, malformed header, non-HTTP scope) is
exercised without a server (audit 001, AUD-005)."""

import json

import pytest
from starlette.types import Message, Scope

from lang_ai_agent.api.limits import BodySizeLimit


class _Downstream:
    """Stands in for the wrapped ASGI app: records the scopes it was given."""

    def __init__(self) -> None:
        self.scopes: list[Scope] = []

    async def __call__(self, scope: Scope, receive: object, send: object) -> None:
        self.scopes.append(scope)


def _http_scope(content_length: str | None) -> Scope:
    headers = [(b"content-length", content_length.encode())] if content_length is not None else []
    return {"type": "http", "method": "POST", "path": "/threads/x/messages", "headers": headers}


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def test_an_oversized_content_length_is_refused_without_reaching_the_app() -> None:
    downstream = _Downstream()
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await BodySizeLimit(downstream, max_bytes=10)(_http_scope("11"), _receive, send)

    assert downstream.scopes == []
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413
    body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body")
    assert "limit is 10 bytes" in json.loads(body)["detail"]


@pytest.mark.parametrize("content_length", ["10", "0", "not-a-number", None])
async def test_other_requests_pass_through(content_length: str | None) -> None:
    downstream = _Downstream()

    async def send(message: Message) -> None:  # pragma: no cover - never called on pass-through
        raise AssertionError(message)

    await BodySizeLimit(downstream, max_bytes=10)(_http_scope(content_length), _receive, send)

    assert len(downstream.scopes) == 1


async def test_non_http_scopes_pass_through_untouched() -> None:
    downstream = _Downstream()

    async def send(message: Message) -> None:  # pragma: no cover - never called on pass-through
        raise AssertionError(message)

    await BodySizeLimit(downstream)({"type": "lifespan"}, _receive, send)

    assert [scope["type"] for scope in downstream.scopes] == ["lifespan"]

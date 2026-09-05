"""Request-size limits (docs/DESIGN.md §5 — audit 001, AUD-005).

Three layers, smallest scope first:

- `MAX_MESSAGE_CHARS` / `MAX_COMMENT_CHARS`: Pydantic `max_length` on the
  request models (422 when exceeded).
- `BodySizeLimit`: a pure ASGI middleware that refuses a request whose
  `Content-Length` exceeds `MAX_BODY_BYTES` with 413 *before* the body is
  read into memory. Pure ASGI rather than `BaseHTTPMiddleware` so the SSE
  streaming responses pass through untouched.
- A chunked body without `Content-Length` is bounded only by the field
  limits, after it has been read — acceptable for a single-token API
  whose clients are the operator's own.
"""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_MESSAGE_CHARS = 8_000
"""Longest `content` a `/messages` request may carry."""

MAX_COMMENT_CHARS = 2_000
"""Longest `comment` an `/approve` request may carry."""

MAX_BODY_BYTES = 64 * 1024
"""Largest request body the server reads at all."""


class BodySizeLimit:
    """Refuse oversized request bodies by `Content-Length` before reading them."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            length = Headers(scope=scope).get("content-length")
            if length is not None and length.isdigit() and int(length) > self.max_bytes:
                response = JSONResponse(
                    {
                        "detail": (
                            f"Request body is {length} bytes; the limit is {self.max_bytes} "
                            "bytes. Send a shorter message or comment."
                        )
                    },
                    status_code=413,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

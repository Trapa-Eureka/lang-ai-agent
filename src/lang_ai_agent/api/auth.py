"""Bearer-token auth (docs/DESIGN.md §5) — one shared token, `APP_BEARER_TOKEN`.

v0.1 deliberately has a single static token rather than per-user auth
(SPEC §3: multi-tenancy is out of scope). `require_bearer_token()` is a
factory so app.py can inject the configured token and tests can inject a
known one — auth never reads the environment itself.
"""

import secrets
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# auto_error=False so a missing header reaches our own 401 below (with a
# fix in the message, per CLAUDE.md) instead of FastAPI's generic 403.
_bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(expected_token: str) -> Callable[..., Awaitable[None]]:
    """Build a FastAPI dependency that rejects any request whose
    `Authorization: Bearer <token>` doesn't match `expected_token`.
    """
    if not expected_token:
        raise ValueError(
            "APP_BEARER_TOKEN is empty. Set it in .env to a non-empty secret "
            "(see .env.example) — an empty token would let every request through."
        )

    async def _check(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    ) -> None:
        # Constant-time comparison (audit 001, AUD-008); bytes, so a non-ASCII
        # header value can't turn into a TypeError-turned-500.
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), expected_token.encode()
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Missing or invalid bearer token. Send "
                    "`Authorization: Bearer <APP_BEARER_TOKEN>` with the token from .env."
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

    return _check

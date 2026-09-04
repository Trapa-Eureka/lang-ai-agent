"""Effect execution — the SEND_MODE double-gate for side-effecting actions.

An effect tool reaching this module has already passed the graph's human
approval interrupt (core/graph.py, built in T5) — that is gate one. Gate two
lives here: `SEND_MODE` must also be `"live"`, or nothing real happens (see
docs/DESIGN.md §8 and CLAUDE.md guardrail 3). Tests always exercise the
dry_run path; only `make smoke` (human only, per docs/WORKFLOW.md §4)
exercises the live path.

v0.1 has no real email provider wired up (see docs/SPEC.md) — this
reference implementation's "live" send is simulated via a structured log
line, so the approval-gate architecture can be demonstrated end to end
without needing real credentials. Swap `Effects._send_live` out first if
this is ever expanded to integrate a real provider.
"""

import logging
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SendMode(StrEnum):
    """Gate two of the double-gate. See `.env.example`'s `SEND_MODE`."""

    DRY_RUN = "dry_run"
    LIVE = "live"


class EmailSendResult(BaseModel):
    sent: bool
    detail: str


class SupportsSendEmail(Protocol):
    """What a `send_reorder_email` tool needs from its effects backend.

    `Effects` implements this for real use; `tests/helpers/mock_effects.py`'s
    `MockEffects` implements it for tests — structurally, with no shared
    base class, so tests don't inherit any of Effects' own gating logic.
    """

    def send_email(  # pragma: no cover
        self, *, to: str, subject: str, body: str
    ) -> EmailSendResult: ...


class Effects:
    """Effect execution, gated by `SEND_MODE`.

    Reading `SEND_MODE` from the environment is the wiring layer's job (a
    later task) — this class only takes the already-resolved mode, so it
    stays trivially testable.
    """

    def __init__(self, send_mode: SendMode = SendMode.DRY_RUN) -> None:
        self.send_mode = send_mode

    def send_email(self, *, to: str, subject: str, body: str) -> EmailSendResult:
        if self.send_mode is not SendMode.LIVE:
            return EmailSendResult(sent=False, detail=f"[dry_run] would send to {to}: {subject}")
        return self._send_live(to=to, subject=subject, body=body)

    def _send_live(self, *, to: str, subject: str, body: str) -> EmailSendResult:
        logger.info("EMAIL SENT to=%s subject=%s", to, subject)
        return EmailSendResult(sent=True, detail=f"sent to {to}: {subject}")

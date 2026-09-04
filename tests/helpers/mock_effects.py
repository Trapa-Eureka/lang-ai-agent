"""MockEffects — records calls instead of doing anything (docs/TESTING.md §2).

Swaps in for `adapters.effects.Effects` in tests, satisfying the same
`SupportsSendEmail` protocol structurally (no shared base class, so tests
don't inherit any of Effects' own gating logic — they get their own,
independently-verifiable copy of it). This is what makes the SEND_MODE
double-gate testable (docs/TESTING.md §4): a test can assert
`send_email_calls` has an entry (the tool ran) while `live_send_calls`
stays empty (the real send path was never entered) when `send_mode` is
`dry_run`.
"""

from lang_ai_agent.adapters.effects import EmailSendResult, SendMode


class EmailCall(dict[str, str]):
    """A recorded `send_email` call — plain dict subclass for easy assertions."""


class MockEffects:
    def __init__(self, send_mode: SendMode = SendMode.DRY_RUN) -> None:
        self.send_mode = send_mode
        self.send_email_calls: list[EmailCall] = []
        self.live_send_calls: list[EmailCall] = []

    def send_email(self, *, to: str, subject: str, body: str) -> EmailSendResult:
        call = EmailCall(to=to, subject=subject, body=body)
        self.send_email_calls.append(call)
        if self.send_mode is not SendMode.LIVE:
            return EmailSendResult(sent=False, detail=f"[dry_run] would send to {to}: {subject}")
        self.live_send_calls.append(call)
        return EmailSendResult(sent=True, detail=f"sent to {to}: {subject}")

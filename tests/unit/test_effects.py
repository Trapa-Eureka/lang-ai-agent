"""Tests for the SEND_MODE double-gate (adapters/effects.py)."""

from lang_ai_agent.adapters.effects import Effects, SendMode


def test_dry_run_never_calls_the_live_send_path() -> None:
    effects = Effects(send_mode=SendMode.DRY_RUN)

    result = effects.send_email(to="ops@example.com", subject="Reorder", body="please reorder")

    assert result.sent is False
    assert "dry_run" in result.detail


def test_live_mode_goes_through_the_send_path() -> None:
    effects = Effects(send_mode=SendMode.LIVE)

    result = effects.send_email(to="ops@example.com", subject="Reorder", body="please reorder")

    assert result.sent is True
    assert result.detail == "sent to ops@example.com: Reorder"


def test_send_mode_defaults_to_dry_run() -> None:
    assert Effects().send_mode is SendMode.DRY_RUN


def test_send_mode_is_a_plain_string_for_env_var_round_tripping() -> None:
    # SEND_MODE comes from an env var (.env.example) as a plain string —
    # StrEnum must compare equal to that string, not just to the enum member.
    assert SendMode.DRY_RUN == "dry_run"
    assert SendMode("live") is SendMode.LIVE

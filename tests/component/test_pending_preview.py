"""PendingAction.args_preview is a summary; the tool runs with the model's
original arguments (docs/DESIGN.md §2/§3 — audit 001, AUD-005)."""

from lang_ai_agent.core.graph import PREVIEW_CHARS
from tests.component.conftest import GraphHarness, MakeHarness
from tests.helpers.scripted_chat_model import script


async def test_long_effect_args_are_previewed_short_but_executed_in_full(
    make_harness: MakeHarness,
) -> None:
    body = "Please reorder 35 units of SKU-100. " * 20  # well past the preview limit
    harness: GraphHarness = make_harness(
        script()
        .tool_call(
            "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": body}
        )
        .final("Sent.")
        .build()
    )

    _, result = await harness.run("send it")
    interrupt = result["interrupt"]
    pending = interrupt.value["action"]

    preview_body = pending.args_preview["body"]
    assert isinstance(preview_body, str)
    assert len(preview_body) == PREVIEW_CHARS and preview_body.endswith("…")
    assert pending.args_preview["to"] == "ops@example.com"  # short values pass through
    assert interrupt.value["draft"] == body  # the human still reviews the whole draft

    await harness.resume(approved=True)

    assert harness.effects.send_email_calls == [
        {"to": "ops@example.com", "subject": "Reorder", "body": body}
    ]
    harness.model.assert_exhausted()

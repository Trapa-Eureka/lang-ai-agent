"""Usage accumulation (T8 completion criterion; docs/TESTING.md §4 "usage"):
across multiple model calls the graph's cumulative `Usage` must equal the
sum of the script's fixed per-turn usage, and that total must surface
through both exposure paths — the `usage` SSE event and thread state.
"""

from lang_ai_agent.api.sse import UsageEvent
from lang_ai_agent.core.state import Usage
from tests.component.conftest import GraphHarness, MakeHarness
from tests.helpers.scripted_chat_model import script

# two model calls: a tool-calling turn, then the final answer
_SCRIPT = (
    script()
    .tool_call("check_stockout", {"store": "main"}, input_tokens=100, output_tokens=10)
    .final("2 items at risk.", input_tokens=150, output_tokens=20)
    .build()
)
_EXPECTED = Usage(input_tokens=250, output_tokens=30, calls=2)


async def test_state_usage_is_the_sum_of_every_model_call(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(_SCRIPT)

    await harness.run("what's at risk?")

    assert (await harness.state_values())["usage"] == _EXPECTED
    harness.model.assert_exhausted()


async def test_usage_sse_event_carries_the_same_total(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(_SCRIPT)

    events = await harness.sse_run("what's at risk?")

    usage_events = [e for e in events if isinstance(e, UsageEvent)]
    assert len(usage_events) == 1
    assert usage_events[0].usage == _EXPECTED


async def test_usage_keeps_accumulating_across_an_interrupt_and_resume(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness(
        script()
        .tool_call(
            "send_reorder_email",
            {"to": "ops@example.com", "subject": "Reorder", "body": "b"},
            input_tokens=40,
            output_tokens=4,
        )
        .final("Sent it.", input_tokens=60, output_tokens=6)
        .build()
    )
    await harness.run("reorder please")
    paused = (await harness.state_values())["usage"]

    await harness.resume(approved=True)

    assert paused == Usage(input_tokens=40, output_tokens=4, calls=1)
    assert (await harness.state_values())["usage"] == Usage(
        input_tokens=100, output_tokens=10, calls=2
    )


async def test_a_turn_without_usage_metadata_still_counts_the_call(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness(script().final("hi").build())

    await harness.run("hello")

    assert (await harness.state_values())["usage"] == Usage(
        input_tokens=0, output_tokens=0, calls=1
    )

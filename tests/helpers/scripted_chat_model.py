"""ScriptedChatModel and its script() builder (see docs/TESTING.md §2).

The core Shift-Left move for this repo: replace the real model with a
deterministic script, so the graph becomes a plain state machine that can be
tested completely without a network call or an LLM in the loop.

Never make the model plausible when a test goes wrong — a scripted test
model must fail loudly the moment its script stops matching reality (see
CLAUDE.md guardrail 5, "플레이키의 씨앗 차단"). This module gives that failure
two shapes:

- `ScriptExhaustedError`, raised mid-test, the moment the graph asks the
  model for one more turn than the script has.
- `ScriptedChatModel.assert_exhausted()`, called at the end of a test, which
  fails if the script had turns nobody ever consumed (the graph took a
  shorter path than the test assumed).
"""

from collections.abc import Callable, Sequence
from typing import Any, NamedTuple, override

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool


class ScriptExhaustedError(RuntimeError):
    """The graph called ScriptedChatModel more times than its script allows."""


class ScriptedToolCall(NamedTuple):
    """One tool call to script into a turn. Use with `ScriptBuilder.tool_calls()`
    for a turn that calls more than one tool at once (docs/TESTING.md §3,
    "혼합 tool_calls").
    """

    name: str
    args: dict[str, Any]
    tool_call_id: str | None = None


class ScriptedChatModel(BaseChatModel):
    """Deterministic stand-in for a real chat model in tests.

    Replays `script` in order, one AIMessage per call — including any
    tool_calls on that message, so the graph reacts to them exactly as it
    would to a real model's tool-calling response. Never makes a network
    call.
    """

    script: list[AIMessage]
    cursor: int = 0
    """How many scripted turns have been consumed so far."""

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next_turn())])

    @override
    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next_turn())])

    def _next_turn(self) -> AIMessage:
        if self.cursor >= len(self.script):
            raise ScriptExhaustedError(
                f"ScriptedChatModel script exhausted after {self.cursor} call(s) — "
                "the graph asked the model for another turn the script doesn't "
                "have. Either add another scripted response, or this is a real "
                "bug making an unexpected extra model call."
            )
        message = self.script[self.cursor]
        self.cursor += 1
        return message

    def assert_exhausted(self) -> None:
        """Fail if the script has turns the graph never consumed.

        Call this at the end of a test alongside the graph's expected final
        state, so a graph that stops early (fewer turns than the test
        assumed) fails the test instead of silently passing.
        """
        if self.cursor < len(self.script):
            remaining = len(self.script) - self.cursor
            raise AssertionError(
                f"ScriptedChatModel has {remaining} unconsumed scripted turn(s) "
                f"({self.cursor}/{len(self.script)} consumed) — the graph took "
                "fewer turns than this test's script assumes."
            )

    @override
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        # The script already encodes any tool_calls a test wants replayed;
        # which tools are "bound" doesn't change what gets replayed, so
        # there's nothing to model here beyond returning this same instance.
        return self

    @property
    @override
    def _llm_type(self) -> str:
        return "scripted-chat-model"


def script() -> "ScriptBuilder":
    """Start building a script for ScriptedChatModel (docs/TESTING.md §2).

    Example:
        script().tool_call("check_stockout", {"store": "main"}).final("...").build()
    """
    return ScriptBuilder()


class ScriptBuilder:
    """Fluent builder for a ScriptedChatModel script.

    Each call appends one more scripted model turn. Chain calls in the
    order the graph is expected to call the model, then `.build()` for the
    `list[AIMessage]` to pass to `ScriptedChatModel(script=...)`.
    """

    def __init__(self) -> None:
        self._turns: list[AIMessage] = []
        self._next_id = 1

    def tool_call(
        self, name: str, args: dict[str, Any], *, tool_call_id: str | None = None
    ) -> "ScriptBuilder":
        """Append a turn where the model calls exactly one tool."""
        return self.tool_calls([ScriptedToolCall(name, args, tool_call_id)])

    def tool_calls(self, calls: Sequence[ScriptedToolCall]) -> "ScriptBuilder":
        """Append a turn where the model calls multiple tools at once.

        Use this (rather than chaining `.tool_call()` twice) for the
        "safe + effect in the same turn" scenario in docs/TESTING.md §3 —
        chaining `.tool_call()` twice would script two *separate* turns.
        """
        turn: list[ToolCall] = [
            {
                "name": call.name,
                "args": call.args,
                "id": call.tool_call_id or self._fresh_id(),
                "type": "tool_call",
            }
            for call in calls
        ]
        self._turns.append(AIMessage(content="", tool_calls=turn))
        return self

    def final(self, content: str) -> "ScriptBuilder":
        """Append a turn where the model responds with plain text and no tool calls."""
        self._turns.append(AIMessage(content=content))
        return self

    def _fresh_id(self) -> str:
        tool_call_id = f"call_{self._next_id}"
        self._next_id += 1
        return tool_call_id

    def build(self) -> list[AIMessage]:
        """Return the assembled script, in order."""
        return list(self._turns)

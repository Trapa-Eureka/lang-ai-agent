"""ScriptedChatModel and its script() builder (see docs/TESTING.md §2).

The core Shift-Left move for this repo: replace the real model with a
deterministic script, so the graph becomes a plain state machine that can be
tested completely without a network call or an LLM in the loop.

Never make the model plausible when a test goes wrong — a scripted test
model must fail loudly the moment its script stops matching reality (see
CLAUDE.md guardrail 5, "block the seed of flakiness"). This module gives that failure
two shapes:

- `ScriptExhaustedError`, raised mid-test, the moment the graph asks the
  model for one more turn than the script has.
- `ScriptedChatModel.assert_exhausted()`, called at the end of a test, which
  fails if the script had turns nobody ever consumed (the graph took a
  shorter path than the test assumed).
"""

from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Any, NamedTuple, override

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.messages.tool import ToolCall
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool


class ScriptExhaustedError(RuntimeError):
    """The graph called ScriptedChatModel more times than its script allows."""


class ScriptedToolCall(NamedTuple):
    """One tool call to script into a turn. Use with `ScriptBuilder.tool_calls()`
    for a turn that calls more than one tool at once (docs/TESTING.md §3,
    "Mixed tool_calls").
    """

    name: str
    args: dict[str, Any]
    tool_call_id: str | None = None


def _chunks_for(message: AIMessage) -> list[AIMessageChunk]:
    """Split a scripted turn into a few streaming chunks (docs/TESTING.md §4:
    the SSE mapper's "token*" events need something to stream from — a
    non-streaming `_generate`/`_agenerate`-only model can't exercise that
    path at all).

    A tool-calling turn streams as a single chunk (this repo's SSE mapper
    only cares that tool_calls arrive *some time* during the stream, not
    how a real provider might dribble out partial JSON args — see T6).
    A plain-text turn streams word by word, so tests can assert multiple
    token events arrived in order.

    The turn's `usage_metadata` rides on the *last* chunk only: LangChain
    sums usage across chunks when it reassembles the message, so attaching
    it to every chunk would multiply it (T8 — this is how the graph's
    usage accumulation sees a scripted turn's fixed token counts under the
    streaming path the API actually uses).
    """
    if message.tool_calls:
        chunks = [AIMessageChunk(content=message.content, tool_calls=message.tool_calls)]
    elif not isinstance(message.content, str) or not message.content:
        chunks = [AIMessageChunk(content=message.content)]
    else:
        words = message.content.split(" ")
        chunks = [
            AIMessageChunk(content=word if i == 0 else f" {word}") for i, word in enumerate(words)
        ]
    chunks[-1].usage_metadata = message.usage_metadata
    return chunks


def _usage(input_tokens: int | None, output_tokens: int | None) -> UsageMetadata | None:
    """Fixed per-turn usage for a scripted turn (docs/TESTING.md §2 "fixed usage")."""
    if input_tokens is None and output_tokens is None:
        return None
    inputs, outputs = input_tokens or 0, output_tokens or 0
    return UsageMetadata(input_tokens=inputs, output_tokens=outputs, total_tokens=inputs + outputs)


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

    @override
    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for chunk in _chunks_for(self._next_turn()):
            yield ChatGenerationChunk(message=chunk)

    @override
    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in _chunks_for(self._next_turn()):
            yield ChatGenerationChunk(message=chunk)

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
        self,
        name: str,
        args: dict[str, Any],
        *,
        tool_call_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> "ScriptBuilder":
        """Append a turn where the model calls exactly one tool."""
        return self.tool_calls(
            [ScriptedToolCall(name, args, tool_call_id)],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def tool_calls(
        self,
        calls: Sequence[ScriptedToolCall],
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> "ScriptBuilder":
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
        self._turns.append(
            AIMessage(
                content="", tool_calls=turn, usage_metadata=_usage(input_tokens, output_tokens)
            )
        )
        return self

    def final(
        self,
        content: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> "ScriptBuilder":
        """Append a turn where the model responds with plain text and no tool calls.

        `input_tokens`/`output_tokens` pin the turn's usage_metadata so a
        test can assert the graph's cumulative `Usage` equals a known sum.
        """
        self._turns.append(
            AIMessage(content=content, usage_metadata=_usage(input_tokens, output_tokens))
        )
        return self

    def _fresh_id(self) -> str:
        tool_call_id = f"call_{self._next_id}"
        self._next_id += 1
        return tool_call_id

    def build(self) -> list[AIMessage]:
        """Return the assembled script, in order."""
        return list(self._turns)

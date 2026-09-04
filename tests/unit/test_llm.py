"""Tests for the chat model factory (T4 completion criteria)."""

import pytest
from langchain_anthropic import ChatAnthropic

from lang_ai_agent.adapters.llm import DEFAULT_MODEL, build_chat_model

# None of these construct a real network client call — init_chat_model only
# builds the client object; it never calls the provider's API (guardrail 2).


def test_builds_the_explicitly_requested_model() -> None:
    model = build_chat_model("anthropic:claude-sonnet-4-5")

    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-sonnet-4-5"


def test_defaults_to_default_model_when_unset() -> None:
    model = build_chat_model()

    assert isinstance(model, ChatAnthropic)
    assert model.model == DEFAULT_MODEL.split(":", 1)[1]


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_model_falls_back_to_default_instead_of_erroring(blank: str) -> None:
    model = build_chat_model(blank)

    assert isinstance(model, ChatAnthropic)
    assert model.model == DEFAULT_MODEL.split(":", 1)[1]


def test_invalid_model_string_raises_an_error_with_a_fix() -> None:
    with pytest.raises(ValueError) as exc_info:
        build_chat_model("not-a-valid-model-string-at-all")

    message = str(exc_info.value)
    assert "MODEL" in message
    assert ".env" in message
    assert DEFAULT_MODEL in message  # the example fix is concrete, not just "fix it"


def test_unknown_provider_prefix_raises_an_error_with_a_fix() -> None:
    with pytest.raises(ValueError, match="MODEL"):
        build_chat_model("totally-fake-provider:some-model")

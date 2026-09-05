"""Tests for the chat model factory (T4) and the provider table behind the
onboarding startup check (T11, docs/DESIGN.md §8.1)."""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel

from lang_ai_agent.adapters.llm import (
    DEFAULT_MODEL,
    PROVIDERS,
    Provider,
    build_chat_model,
    missing_key_error,
    provider_of,
)

# None of these construct a real network client call — init_chat_model only
# builds the client object; it never calls the provider's API (guardrail 2).


# --- provider table (DESIGN §8.1) -------------------------------------------


@pytest.mark.parametrize("provider", PROVIDERS, ids=[p.id for p in PROVIDERS])
def test_every_table_provider_builds_a_model_and_passes_the_key_check(
    provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table's `id` must be a prefix init_chat_model accepts and its
    `key_env` the variable the SDK actually needs — with a fake key set,
    construction succeeds (no network) and the startup check is silent.
    """
    monkeypatch.setenv(provider.key_env, "test-key-not-real")
    spec = f"{provider.id}:{provider.suggested_model}"

    model = build_chat_model(spec)

    assert isinstance(model, BaseChatModel)
    assert provider_of(spec) is provider
    assert missing_key_error(spec) is None


def test_missing_key_error_names_the_variable_the_model_and_the_init_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    message = missing_key_error("openai:gpt-5")

    assert message is not None
    assert "OPENAI_API_KEY" in message
    assert "openai:gpt-5" in message
    assert "lang-ai-agent init" in message  # the fix, not just the cause


def test_key_check_falls_back_to_the_default_provider_when_model_is_unset() -> None:
    assert "ANTHROPIC_API_KEY" in (missing_key_error(None, env={}) or "")
    assert missing_key_error("  ", env={"ANTHROPIC_API_KEY": "k"}) is None


def test_a_blank_key_counts_as_missing() -> None:
    assert missing_key_error(DEFAULT_MODEL, env={"ANTHROPIC_API_KEY": "   "}) is not None


def test_providers_outside_the_table_are_not_key_checked() -> None:
    # init_chat_model may well support these; we just don't know their env var.
    assert provider_of("ollama:llama3") is None
    assert provider_of("claude-sonnet-4-5") is None  # unprefixed = inferred by LangChain
    assert missing_key_error("ollama:llama3", env={}) is None


# --- factory (T4) -------------------------------------------------------------


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

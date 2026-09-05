"""Chat model factory — provider-agnostic via `init_chat_model` (docs/DESIGN.md §1, §8).

The default is Claude (`langchain-anthropic`), but `.env`'s `MODEL` can name
any provider `init_chat_model` supports — see
https://docs.langchain.com/oss/python/integrations/providers.

`PROVIDERS` is the single source for the onboarding table in DESIGN §8.1:
which environment variable holds each provider's key and which model
`lang-ai-agent init` suggests. Providers outside the table still work
through `init_chat_model`; they just can't be key-checked at startup.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

DEFAULT_MODEL = "anthropic:claude-sonnet-4-5"
"""Matches `.env.example`'s MODEL — used whenever MODEL is unset or blank."""


@dataclass(frozen=True)
class Provider:
    """One row of the DESIGN §8.1 onboarding table."""

    id: str
    """The `provider:` prefix `init_chat_model` understands."""
    label: str
    key_env: str
    """The environment variable the provider's SDK reads its API key from."""
    suggested_model: str
    """What `lang-ai-agent init` offers as the default — a suggestion, not a pin."""


PROVIDERS: tuple[Provider, ...] = (
    Provider("anthropic", "Anthropic (Claude)", "ANTHROPIC_API_KEY", "claude-sonnet-4-5"),
    Provider("openai", "OpenAI", "OPENAI_API_KEY", "gpt-5"),
    Provider("xai", "xAI (Grok)", "XAI_API_KEY", "grok-4"),
    Provider("google_genai", "Google (Gemini)", "GOOGLE_API_KEY", "gemini-2.5-pro"),
)
"""Anthropic first: it is the default provider, so it is option 1 in `init` too."""


def resolve_model(model: str | None) -> str:
    """`model` with the None/blank fallback applied — the MODEL actually used."""
    return (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def provider_of(model: str | None) -> Provider | None:
    """The `PROVIDERS` row for `model`'s `provider:` prefix, or None when the
    prefix isn't in the table. An unprefixed model name counts as unknown
    too — `init_chat_model` infers those itself, but we can't key-check them.
    """
    prefix, _, _ = resolve_model(model).partition(":")
    return next((provider for provider in PROVIDERS if provider.id == prefix), None)


def missing_key_error(model: str | None, env: Mapping[str, str] | None = None) -> str | None:
    """The startup-check message when `model`'s provider key is missing or
    blank in `env` (default: the process environment) — None when it's set,
    or when the provider isn't in `PROVIDERS` and so can't be checked.
    """
    provider = provider_of(model)
    if provider is None:
        return None
    values = os.environ if env is None else env
    if values.get(provider.key_env, "").strip():
        return None
    return (
        f"{provider.key_env} is not set, but MODEL={resolve_model(model)} uses {provider.label}.\n"
        "Fix: run `lang-ai-agent init` to write .env interactively, or add "
        f"{provider.key_env}=<your key> to .env (see .env.example)."
    )


def build_chat_model(model: str | None = None) -> BaseChatModel:
    """Build the chat model named by `model` (a `"provider:model"` string).

    Falls back to `DEFAULT_MODEL` if `model` is `None` or blank. Raises
    `ValueError` naming both the cause and the fix if `model` can't be
    resolved to a concrete chat model — never lets a misconfigured `MODEL`
    surface as a generic, `.env`-oblivious error (CLAUDE.md's error-message
    convention).
    """
    # An empty/blank string steers init_chat_model to a deferred
    # "configurable" model instead of raising — sanitize it away first so
    # we always get a concrete, immediately-usable BaseChatModel back.
    resolved = resolve_model(model)

    try:
        return init_chat_model(resolved)
    except ValueError as e:
        raise ValueError(
            f"Could not build a chat model from MODEL={resolved!r}: {e}\n"
            "Fix: set MODEL in .env to a 'provider:model' string, e.g. "
            f"MODEL={DEFAULT_MODEL} (see .env.example)."
        ) from e

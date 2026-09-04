"""Chat model factory — provider-agnostic via `init_chat_model` (docs/DESIGN.md §1, §8).

The default is Claude (`langchain-anthropic`), but `.env`'s `MODEL` can name
any provider `init_chat_model` supports — see
https://docs.langchain.com/oss/python/integrations/providers.
"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

DEFAULT_MODEL = "anthropic:claude-sonnet-4-5"
"""Matches `.env.example`'s MODEL — used whenever MODEL is unset or blank."""


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
    resolved = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    try:
        return init_chat_model(resolved)
    except ValueError as e:
        raise ValueError(
            f"Could not build a chat model from MODEL={resolved!r}: {e}\n"
            "Fix: set MODEL in .env to a 'provider:model' string, e.g. "
            f"MODEL={DEFAULT_MODEL} (see .env.example)."
        ) from e

"""lang_ai_agent — LangGraph-based agent backend with a human-approval gate.

See docs/SPEC.md and docs/DESIGN.md for the product spec and technical design.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # pyproject.toml is the single source of the version (docs/DESIGN.md §10):
    # read it from the installed metadata instead of repeating it here.
    __version__ = version("lang-ai-agent")
except PackageNotFoundError:  # pragma: no cover - imported from a checkout that was never installed
    __version__ = "0.0.0"

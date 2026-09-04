"""Deliberately type-broken fixture — never imported by application or test code.

Excluded from the main pyright scan via `[tool.pyright].exclude` in
pyproject.toml, so it never breaks `make typecheck` / `make check`. Its only
job is to give tests/unit/test_typecheck_strict.py a real error to detect.
"""


def add(a: int, b: int) -> int:
    return a + b


add("1", "2")  # wrong argument types — pyright strict must flag this

"""Regression guard: pyright strict mode must actually catch type errors.

T0 completion criteria (docs/TASKS.md) calls this out explicitly: a snapshot
test proving strict mode is enforced, not just nominally configured. If
someone weakens `[tool.pyright].typeCheckingMode` in pyproject.toml, this
test fails loudly instead of the config silently rotting — see CLAUDE.md's
`# type: ignore` discipline, which only means something if strict mode is
truly on.

`tests/typecheck_fixtures/` is excluded from the root project's pyright scan
(pyproject.toml `[tool.pyright].exclude`) so the deliberately-broken fixture
never fails `make typecheck`/`make check`. Pyright honors that `exclude` even
for a file passed explicitly on the CLI, so this test points `--project` at
a small standalone pyrightconfig.json (no excludes) that lives next to the
fixture, dedicated to this one check.
"""

import json
import subprocess
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent.parent / "typecheck_fixtures"
FIXTURE = FIXTURES_DIR / "bad_typed.py"
FIXTURE_PROJECT = FIXTURES_DIR / "pyrightconfig.json"


def test_pyright_strict_catches_intentional_type_error() -> None:
    assert FIXTURE.exists(), f"fixture missing: {FIXTURE}"
    assert FIXTURE_PROJECT.exists(), f"fixture pyright config missing: {FIXTURE_PROJECT}"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyright",
            "--project",
            str(FIXTURE_PROJECT),
            "--outputjson",
            str(FIXTURE),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    report = json.loads(result.stdout)
    summary = report["summary"]

    assert summary["filesAnalyzed"] == 1, (
        "expected pyright to analyze exactly the fixture file, "
        f"got: {summary}\nstdout:\n{result.stdout}"
    )
    assert summary["errorCount"] > 0, (
        "pyright reported 0 errors on a file with a known type error — "
        f"strict mode is not actually being enforced.\nreport: {report}"
    )

# RELEASE — PyPI release runbook (Trusted Publishing)

Settled (2026-09-05, user): license **MIT**; release auth is **GitHub Actions Trusted Publishing (OIDC)**. No long-lived API token is created or stored. Design rationale in DESIGN §10; the maintainer-approval gate for production releases in WORKFLOW §4.

## 0. Why a release costs the developer nothing (facts)

| Item | Who bears it | Basis |
|---|---|---|
| Package storage and downloads | PyPI (run by the PSF, CDN) | `pip install` downloads from PyPI. Not the developer's servers or traffic; free. |
| The installer's runtime and model cost | The installer | Runs on the installer's machine; model calls are billed to **the installer's own API key** entered with `lang-ai-agent init`. |
| The developer's API key | Not in the distribution | The sdist and wheel contain only `src/`, `pyproject.toml` and the README (confirmed in the 2026-09-05 build: no `.env`, `data/`, `mcp_servers.json` or key strings). |
| GitHub Actions minutes | GitHub | Standard runners are free and unlimited for public repos. The workflow reacts only to this repo's events; a fork PR from a first-time contributor does not run without the owner's approval, and the release jobs run only on a tag push by someone with write access. |

What remains is one project name on PyPI and whether to answer issues.

## 1. One-time human setup (browser)

### 1.1 Accounts

- https://test.pypi.org and https://pypi.org are **separate accounts**. Sign up for both → verify email → set up 2FA (authenticator app or security key). Publisher registration is not possible without 2FA.
- The project name `lang-ai-agent` was free on both indexes as of 2026-09-05 (verified). **Do not create the project in advance** — register a pending publisher, and the first upload creates the project with the publisher attached.

### 1.2 TestPyPI pending publisher

https://test.pypi.org/manage/account/publishing/ → "Add a new pending publisher" → GitHub tab

| Field | Value |
|---|---|
| PyPI Project Name | `lang-ai-agent` |
| Owner | `Trapa-Eureka` |
| Repository name | `lang-ai-agent` |
| Workflow name | `publish.yml` |
| Environment name | `testpypi` |

All five values must match the workflow **exactly**. Workflow name is the file name of `.github/workflows/publish.yml` only (no path), case-sensitive.

### 1.3 PyPI pending publisher

https://pypi.org/manage/account/publishing/ → same values, Environment name `pypi`.

### 1.4 GitHub Environments

Repo Settings → Environments → New environment

- `testpypi`: no protection rules. A test index, within the range the agent releases to autonomously (WORKFLOW §4).
- `pypi`: add yourself under **Required reviewers**. "Deployment branches and tags" → "Selected branches and tags" → add tag rule `v*`. Clicking "Review deployments → Approve" in the Actions UI at release time is the maintainer approval of WORKFLOW §4.

Environment protection rules are available on the free plan because the repo is public.

### 1.5 What not to do

- Do not create or store API tokens. Only in an exceptional case that truly needs a local manual upload, use a **project-scoped** token as an environment variable (outside the repo) and revoke it afterwards. TestPyPI and PyPI tokens are separate.
- Put nothing in GitHub Secrets. The only permission the workflow needs is the job's `id-token: write`.

## 2. What the agent does (T13 · T14)

- **T13**: `pyproject.toml` metadata (`license = "MIT"`, classifiers, urls, keywords) + `LICENSE` (MIT) + `.github/workflows/publish.yml` (build → `testpypi` job) + `uv build` verification + TestPyPI upload and install check with tag `v0.1.0rcN`. The upload step is possible only after §1 is done.
- **T14**: add the `pypi` job to `publish.yml` (runs after `testpypi` succeeds and the `pypi` environment is approved) + one production release (maintainer approval).

Shape of the workflow (implemented in T13; values match §1.2 and §1.3):

```yaml
name: Publish
on:
  push:
    tags: ["v*"]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: test "v$(uv version --short)" = "$GITHUB_REF_NAME"   # tag = pyproject version
      - run: uv build
      - uses: actions/upload-artifact@v4
        with: { name: dist, path: dist/ }
  testpypi:
    needs: build
    runs-on: ubuntu-latest
    environment: { name: testpypi, url: https://test.pypi.org/p/lang-ai-agent }
    permissions:
      id-token: write            # OIDC — no tokens, no secrets
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          repository-url: https://test.pypi.org/legacy/
  pypi:                          # added in T14
    needs: [build, testpypi]
    if: needs.build.outputs.prerelease == 'false'   # final tags only
    runs-on: ubuntu-latest
    environment: { name: pypi, url: https://pypi.org/p/lang-ai-agent }   # Required reviewers
    permissions:
      id-token: write
    steps:                       # same as testpypi, without repository-url
      - uses: actions/download-artifact@v4
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
```

Final vs pre-release decision (T14): the build job's tag-check step computes `packaging.version.Version(version).is_prerelease` and passes it as the job output `prerelease`; the `pypi` job runs only when that value is `false`. Looking for `rc` in the tag string would misclassify `a1`, `b1` and `.dev1` pre-releases as final, so the decision uses PEP 440 parsing. On a pre-release tag the `pypi` job shows as skipped and the workflow succeeds.

## 3. Tag and version rules

- The single source of the version is `version` in `pyproject.toml`. The tag is `v` + that value (`v0.1.0rc1`, `v0.1.0`). The build job fails on a mismatch.
- `vX.Y.ZrcN` (pre-release): TestPyPI only. `vX.Y.Z` (final): TestPyPI → maintainer approval → PyPI.
- Neither PyPI nor TestPyPI accepts **the same version (file) twice**, even after deletion. Trial runs bump the rc number; the final `0.1.0` happens once.

## 4. Release procedure (every time)

1. Bump `version` in `pyproject.toml` → PR → `make check` → merge to main.
2. Tag on main: `git tag v0.1.0rc1 && git push origin v0.1.0rc1`.
3. In Actions, confirm build → testpypi succeeded, then verify the install (TestPyPI does not host the dependencies, so PyPI is attached as an extra index):

```bash
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ "lang-ai-agent==0.1.0rc1"
lang-ai-agent --help
```

4. Final: version `0.1.0` → merge → tag `v0.1.0` → after testpypi succeeds, in the Actions `pypi` job click "Review deployments" → Approve. That click is the production release. Pushing the tag consumes that final version on TestPyPI first (no re-upload), so tag only after the distribution contents — README included — are final.
5. Verify: `pip install lang-ai-agent` → `lang-ai-agent init` → `lang-ai-agent serve`.

## 5. Release log

| Date | Tag | Target | Result |
|---|---|---|---|
| 2026-09-05 | `v0.1.0rc1` (main `e21d000`) | TestPyPI | Publish run 33956057465 succeeded (build → testpypi). Installed into an isolated venv with `--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/`; `lang-ai-agent --help`, version and MIT verified. The pending publisher created the project `lang-ai-agent` on first upload (T13) |
| 2026-09-05 | `v0.1.0` (main `bc1ad58`) | TestPyPI → **PyPI** | Publish run 33957885151 succeeded (build → testpypi → pypi). The `pypi` job ran after the Required reviewer of environment `pypi` (the maintainer) approved via "Review deployments → Approve" in Actions — the first run of the WORKFLOW §4 approval gate. Installed from PyPI into an isolated environment (`uv run --isolated --no-project --with "lang-ai-agent==0.1.0"`); version `0.1.0`, `License-Expression: MIT`, `lang-ai-agent --help` verified. Wheel + sdist registered on pypi.org. This distribution's README carries the pre-T15 wording ("first release are next") and is refreshed by the next version (T14) |
| 2026-09-05 | `v0.1.1` (main `f7f604e`) | TestPyPI → **PyPI** | Publish run 33961109346 succeeded (build → testpypi → pypi, maintainer approval). Purpose: ship the English-only repo and the updated README to the PyPI project page — verified on pypi.org (badges, Install section, "maintainer approval" wording present; no Korean mention). Isolated install of `lang-ai-agent==0.1.1`: version, `License-Expression: MIT`, `lang-ai-agent --help` verified. PyPI's JSON API lagged the upload by about a minute before `0.1.1` appeared (T17) |

## 6. Troubleshooting

- `invalid-publisher`-type errors: one of the five values in §1.2/§1.3 differs from the workflow (case, file name, environment name).
- `File already exists`: that version is already uploaded. Bump the version and tag again.
- The `pypi` job hangs waiting for approval: check that the Required reviewer of environment `pypi` is you and that the tag rule `v*` is correct.
- The TestPyPI install cannot find dependencies: `--extra-index-url https://pypi.org/simple/` is missing.

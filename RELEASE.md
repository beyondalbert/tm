# Releasing The Machine (TM)

## One command (Windows)

The release script runs the checks, bumps the version, builds, commits, pushes,
tags, creates the GitHub Release, and waits for the PyPI workflow:

```powershell
.\scripts\release.ps1                              # patch release
.\scripts\release.ps1 -Bump minor                   # minor release
.\scripts\release.ps1 -Version 1.0.0                # explicit version
.\scripts\release.ps1 -Message "feat(core): add x" -Paths src\tm\x.py tests\test_x.py
.\scripts\release.ps1 -SkipChecks                   # skip ruff/mypy/pytest
```

New files must be named with `-Paths`; only tracked changes are staged by
default. The script publishes through the GitHub Actions workflow and falls back
to a manual `workflow_dispatch` if the release-triggered run fails to start.

GitHub authentication comes from `GITHUB_TOKEN`, `GH_TOKEN`, or the credential
stored for `github.com`. PyPI uses Trusted Publishing; the script never handles a
PyPI token.

## Manual process

The distribution name is `the-machine` (the CLI command is `tm`). It is published
on PyPI at <https://pypi.org/project/the-machine/>.

### 1. Bump the version

`version` lives in `pyproject.toml`:

```bash
uv version --bump patch   # 0.1.0 -> 0.1.1
uv version --bump minor   # 0.1.0 -> 0.2.0
uv version 0.2.0          # set explicitly
```

Commit the version bump.

### 2. Build and check locally

```bash
uv build                 # writes dist/*.whl and dist/*.tar.gz
uvx --from "twine>=7" twine check dist/*   # validates metadata (README, license, classifiers)
```

On Windows, `scripts/release-smoke.ps1` does build + metadata check + installs
the wheel into a throwaway venv and runs `tm --version` / `tm --list-models`:

```powershell
.\scripts\release-smoke.ps1 -Version X.Y.Z
```

### 3. Publish

### Option A - Trusted Publishing (recommended, no tokens)

CI publishes via OIDC; no API tokens are stored. The workflow is
`.github/workflows/publish.yml`.

One-time setup:

1. On PyPI, add a **pending publisher** (Account settings -> Publishing):
   - PyPI Project Name: `the-machine`
   - Owner: `beyondalbert`
   - Repository name: `tm`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
2. In the GitHub repo, create an environment named `pypi`
   (Settings -> Environments). Add `testpypi` too if you want the dry run.
3. For TestPyPI, repeat step 1 at <https://test.pypi.org> with environment
   `testpypi`.

Publish:

- **TestPyPI (dry run):** Actions -> *Publish* -> *Run workflow* ->
  target `testpypi`. Verify with:
  ```bash
  pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "the-machine[all]"
  ```
- **PyPI:** create a GitHub Release for the tag `vX.Y.Z`
  (`git tag v0.1.0 && git push origin v0.1.0`, then "Draft a new release").
  Publishing a release triggers the `pypi` job.

### Option B - Manual with an API token

Create a PyPI API token (Account settings -> API tokens), scoped to the
project, then:

```bash
uv build
UV_PUBLISH_TOKEN=pypi-xxxxxxxx uv publish
```

TestPyPI:

```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token pypi-xxxxxxxx
```

On Windows PowerShell:

```powershell
$env:UV_PUBLISH_TOKEN = "pypi-xxxxxxxx"
uv publish
```

### 4. Verify

```bash
pip install "the-machine[all]"
tm --version
tm --list-models
```

## Notes

- A version is immutable: PyPI rejects re-uploads of the same version. Bump
  before retrying.
- The core install supports OpenAI-compatible providers. `[anthropic]`,
  `[google]`, `[tui]`, and `[all]` add the optional SDK/UI dependencies.
- For a local smoke check without publishing, run `uvx --from . tm --version`.

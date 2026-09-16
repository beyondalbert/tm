# Releasing The Machine (TM)

The distribution name is `the-machine` (the CLI command is `tm`). It is not yet
on PyPI, but the name is currently available.

## 1. Bump the version

`version` lives in `pyproject.toml`:

```bash
uv version --bump patch   # 0.1.0 -> 0.1.1
uv version --bump minor   # 0.1.0 -> 0.2.0
uv version 0.2.0          # set explicitly
```

Commit the version bump.

## 2. Build and check locally

```bash
uv build                 # writes dist/*.whl and dist/*.tar.gz
uvx twine check dist/*   # validates metadata (README, license, classifiers)
```

## 3. Publish

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

## 4. Verify

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

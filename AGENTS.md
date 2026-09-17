# Development Rules

Guidance for humans and coding agents working on TM (The Machine). Borrowed and
adapted from Pi's `AGENTS.md`, tuned for this Python/uv project.

## Conversational Style

- Keep answers short and concise.
- No emojis in commits, issues, PR comments, or code.
- No fluff or cheerful filler text. Technical prose only, be direct.
- Use concise, clear, simple language. Define unavoidable jargon before using it.
- Explain non-trivial designs as: problem, concrete example or short trace, then
  solution. State why the solution is necessary and distinguish it from optional
  complexity.
- When the user asks a question, answer it first before making edits.
- When responding to feedback or an analysis, say whether you agree or disagree
  before describing what you changed.

## Code Quality

- Read files in full before wide-ranging changes and when asked to investigate or
  audit. Do not rely on search snippets for broad changes.
- Prefer explicit types. `mypy` is configured (non-strict) and must stay clean.
- No bare `except:`; catch specific exceptions. Use `contextlib.suppress` for
  intentional ignores.
- Inline single-use helpers rather than exporting them.
- Keep public surface small; add to `__all__` only what callers need.
- Never downgrade code to silence a type error; fix the type or the dependency.
- Do not add backward compatibility shims unless the user asks.
- Ask before removing functionality that appears intentional.

## Commands

- After code changes (not docs): `uv run ruff check src tests` and
  `uv run mypy src tests`. Fix all findings before finishing.
- Run tests with `uv run pytest -q`. For a focused file:
  `uv run pytest tests/test_agent.py -q`.
- Evals (real/faux providers, may need network): `uv run pytest tests/evals -q -m eval`.
- Do not run a bare `uv run pytest` against the eval suite without provider keys;
  it skips LLM-dependent tests automatically.
- After publishing-related changes, verify with `scripts/release-smoke.ps1`.

## Versioning and Releases

- `version` lives in `pyproject.toml` only. Bump with `uv version --bump patch`.
- One version is immutable on PyPI: bump before retrying a failed upload.
- See `RELEASE.md` for the full process. Prefer Trusted Publishing in CI; the
  manual path uses `UV_PUBLISH_TOKEN`.
- Never commit a real PyPI token or API key. Credentials live in the OS key store
  or a user-scoped env var, never in the repo.

## Git

Multiple agent sessions may run in the same working directory.

Committing:

- Only commit files YOU changed in THIS session.
- Stage explicit paths (`git add <path1> <path2>`); never `git add -A` /
  `git add .`.
- Run `git status` before committing and verify you are only staging your files.
- Message format: `{feat,fix,docs,test,chore}(<area>): <summary>`, informative and
  concise. `<area>` is one of `ai`, `core`, `cli`, `tui`, `tools`, `telemetry`,
  `evals`, `release`.
- Never commit unless the user asks.

Never run (destroys other agents' work or bypasses checks):

- `git reset --hard`, `git checkout .`, `git clean -fd`, `git stash`,
  `git add -A`, `git add .`, `git commit --no-verify`, `git push --force`.

## Security

- `src/tm/permissions/` is the trust boundary for filesystem, shell, and network
  access. Changes there affect every tool call.
- Telemetry must never carry prompts, message content, tool arguments, or tool
  output. Only structural metadata and counts. See `src/tm/telemetry/`.
- Extensions and hooks run with full process permissions. Hook side effects must
  be idempotent; a crash may rerun a hook. See `src/tm/extensions.py`.

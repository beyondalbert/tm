[English](README.md) | [简体中文](README.zh-CN.md)

# The Machine (TM)

[![PyPI](https://img.shields.io/pypi/v/the-machine.svg)](https://pypi.org/project/the-machine/)
[![Python versions](https://img.shields.io/pypi/pyversions/the-machine.svg)](https://pypi.org/project/the-machine/)
[![CI](https://github.com/beyondalbert/tm/actions/workflows/ci.yml/badge.svg)](https://github.com/beyondalbert/tm/actions/workflows/ci.yml)

A local AI agent that can control your machine, extensible and permission-gated.

TM is a Python agent harness inspired by the architecture of [pi](../pi), rebuilt
around explicit permission approval for filesystem, shell, and network access.

## Status

Core agent, providers, built-in tools, permissions, and CLI are working. See
the phase table below.

## Installation

`the-machine` is on [PyPI](https://pypi.org/project/the-machine/). [uv](https://docs.astral.sh/uv/)
is recommended (it can also install Python for you).

Install uv (once):

```powershell
# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install the `tm` command (recommended)

```bash
uv tool install "the-machine[all]"
uv tool update-shell   # add the tool bin dir to PATH; restart the terminal afterwards
tm --help
```

Or with pip, inside a virtual environment:

```bash
pip install "the-machine[all]"
```

`[all]` pulls in the Anthropic, Google, and TUI extras. Drop it for a minimal
install (OpenAI-compatible providers only; `--tui` then needs `textual`).

### From source

```bash
uv tool install "the-machine[all] @ git+https://github.com/beyondalbert/tm"
# without git:
uv tool install "the-machine[all] @ https://github.com/beyondalbert/tm/archive/refs/heads/main.tar.gz"
```

### From a clone (development)

```bash
git clone https://github.com/beyondalbert/tm
cd tm
uv sync --all-extras
uv run tm --help
```

### First run

```bash
tm --login deepseek        # store an API key (input visible)
tm "list the files in this folder"
```

Requires Python 3.11+. uv installs a suitable interpreter automatically.

### Updating

```bash
tm --update
```

`tm --update` checks PyPI and upgrades in place. It uses whichever installer owns
the environment (uv tool, pipx, or pip), so uv is not required. On Windows the
running `tm.exe` cannot replace itself, so the upgrade runs in a small detached
helper (log: `<config>/update.log`); exit tm and start it again when it finishes.

## Providers

Multi-provider via official SDK adapters. Domestic providers first (DeepSeek,
Qwen, Kimi, Zhipu GLM), then OpenAI and any OpenAI-compatible endpoint
(Ollama, vLLM, LM Studio).

Set the API key for the provider you want, then run:

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
python -m uv run tm "list the files in this folder"
```

### Where to set the API key

Three options, in order of convenience:

1. **Store it once (recommended)**. Prompts for the key (visible, so you can
   confirm it) and saves it to the credentials file:

   ```powershell
   python -m uv run tm --login deepseek
   ```

   After saving, TM echoes back a masked preview (for example
   `sk-000...0000 (35 chars)`) so you can confirm it was entered correctly.

   Saved to `<config>/credentials.toml`:
   - Windows: `%APPDATA%\the-machine\credentials.toml`
   - Linux/macOS: `~/.config/the-machine/credentials.toml`

   The file looks like:

   ```toml
   [providers]
   deepseek = "sk-..."
   qwen = "sk-..."
   ```

2. **Environment variable** (session or persistent):

   ```powershell
   # current shell only
   $env:DEEPSEEK_API_KEY = "sk-..."
   # persistent for your user (restart the terminal afterwards)
   [Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-...", "User")
   ```

3. **Override the config location** with `TM_CONFIG_DIR` if you do not want the
   default directory.

Stored credentials take precedence over environment variables.

Providers and env vars:

| Provider | Id | Env var | Default model |
|---|---|---|---|
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | deepseek-v4-pro |
| Qwen (DashScope) | `qwen` | `DASHSCOPE_API_KEY` | qwen-plus |
| Moonshot (Kimi) | `moonshot` | `MOONSHOT_API_KEY` | moonshot-v1-32k |
| Zhipu (GLM) | `zhipu` | `ZHIPUAI_API_KEY` | glm-4-plus |
| SiliconFlow | `siliconflow` | `SILICONFLOW_API_KEY` | DeepSeek-V3 |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | claude-3-5-sonnet-latest |
| Google Gemini | `google` | `GEMINI_API_KEY` | gemini-2.0-flash |
| OpenAI | `openai` | `OPENAI_API_KEY` | gpt-4o |
| Groq | `groq` | `GROQ_API_KEY` | llama-3.3-70b-versatile |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | claude-3.5-sonnet |
| Together AI | `together` | `TOGETHER_API_KEY` | Llama-3.3-70B |
| xAI | `xai` | `XAI_API_KEY` | grok-2-latest |
| Ollama (local) | `ollama` | – | qwen2.5:7b |

Anthropic and Google use their official SDKs; the rest use the OpenAI-compatible
adapter. `ollama` and other local servers need no key.

List models: `tm --list-models`.

### Custom providers and models

Define your own provider (any OpenAI-compatible, Anthropic, or Google endpoint)
or extra models in `<config>/models.toml` (Windows:
`%APPDATA%\the-machine\models.toml`). It is merged over the built-in catalog, so
`tm --list-models` and `/model` pick it up without editing code.

```toml
[providers.myprovider]
name = "My Provider"
api = "openai-completions"          # or anthropic-messages / google-generative-ai
base_url = "https://api.example.com/v1"
api_key_env = "MYPROVIDER_API_KEY"  # a name, or a list of names
default_model = "my-model"

[[providers.myprovider.models]]
id = "my-model"
context_window = 32768
max_tokens = 4096
input_cost = 0.5        # optional: USD per 1M tokens
output_cost = 1.5
cache_read_cost = 0.05

# add or override models on a built-in provider
[[providers.deepseek.models]]
id = "deepseek-v4-lite"
context_window = 65536
```

Store the key with `tm --login myprovider`, or set the env var named above.
Switch to a custom model with `/model my-model` or `tm --model my-model`.

## Modes

| Command | Behavior |
|---|---|
| `tm` | Interactive TUI (Textual) with tools and permissions |
| `tm "prompt"` | One-shot agent run |
| `tm --no-tui` | Plain console REPL instead of the TUI |
| `tm --tui` | Force the TUI (default when interactive) |
| `tm --read-only` | Only read/grep/find/ls tools |
| `tm --no-python` | Disable the python tool (shell stays) |
| `tm --dry-run` | Show what would change without mutating the machine |
| `tm --no-tools` | Plain chat, no machine control |
| `tm --yolo` | Auto-approve every action |
| `tm -p` | One-shot, reads stdin when no prompt is given |
| `tm --json` | Emit agent events as JSON lines (for integration) |
| `tm -c` | Continue the most recent session here (the default) |
| `tm --new-session` | Start a fresh session instead of continuing the latest |
| `tm -r` | Pick a saved session to resume (`--all-sessions` to include other dirs) |
| `tm --no-extensions` | Skip loading `.aiagent/extensions` |
| `tm --no-auto-compact` | Disable automatic context compaction |
| `tm --telemetry` | Write redacted spans to `<config>/telemetry.jsonl` |
| `tm --durable` | Persist a durable restart point per run to `<config>/state.jsonl` |
| `tm --no-mouse` | Let the terminal handle mouse selection/copy |
| `tm --approve` | Trust project-local files for this run (`-a`) |
| `tm --no-approve` | Ignore project-local files for this run (`-na`) |

Context files (`AGENTS.md` / `CLAUDE.md`, walking up from cwd, plus the global
config dir) are appended to the system prompt. Disable with `--no-context-files`.

When stdout is not a terminal (piped or redirected) or `textual` is not
installed, `tm` falls back to the console REPL automatically.

**Copying text.** The TUI captures the mouse for in-app selection and scrolling.
Drag to select text and press `Ctrl+Shift+C` to copy it. Copying uses the real
system clipboard (`clip`/`Set-Clipboard` on Windows, `pbcopy`, `wl-copy`, or
`xclip`/`xsel`), with an OSC52 fallback for terminals that support it. With no
selection, `Ctrl+Shift+C` copies the last reply. You can also run `/copy [n]` to
copy the nth-from-last reply. If copying does nothing (some terminals intercept
`Ctrl+Shift+C`), start the TUI with `--no-mouse` and use the terminal's own
selection (Shift+drag on most terminals). Set `mouse = false` in settings to make
that the default.

## Permissions

Every file, shell, and network action is evaluated against a policy. Deny rules
win, then allow rules, then the default decision. **Reads are allowed by
default** (`default_read = "allow"`); writes, shell, and network default to
`ask`. Undecided actions prompt the user; `a` (always) remembers the scope and
persists it to `<config>/approvals.json`, so it survives a restart. All decisions
are appended to `<config>/audit.jsonl`.

To reduce prompts during a session, `/auto on` (or `Ctrl+Y` in the TUI) auto
approves everything that is not explicitly denied for the rest of the session;
`/auto off` returns to prompting, and `tm --yolo` starts in that mode. A built-in
list of destructive commands always asks and is never remembered, as does
`elevate`.

Policy files are merged from `<config>/policy.toml` (global) and
`<cwd>/.aiagent/policy.toml` (project). Example:

```toml
default = "ask"          # writes, shell, network
default_read = "allow"   # reads (set "ask" to be prompted)

[files.read]
allow = ["**"]
deny = ["**/.env", "**/id_rsa"]

[files.write]
allow = ["src/**", "tests/**"]
deny = [".git/**"]

[shell]
allow = ["git *", "ls *", "pytest *"]
deny = ["rm -rf *", "shutdown*", "format *"]

[network]
allow = ["api.deepseek.com"]
```

File rules are **folder-aware**: a rule naming a directory (`src`, `src/`, or
`src/**`) covers everything under it, so you can control access per folder
instead of per file. When the policy leaves an action as `ask` and you answer
`a` (always), TM remembers the **scope**: the containing folder for file actions,
the **program** for shell commands (`git` covers `git status`, `git log`, ...),
and the host for network actions.

Note: shell commands are matched textually. TM cannot reliably stop a shell
command from making network calls; use a sandbox/container when you need a hard
network boundary.

**Project trust.** Project-local resources that can run code or change the policy
(`.aiagent/extensions`, `.aiagent/skills`, `.aiagent/prompts`, `.agents/skills`,
`.aiagent/policy.toml`, `.aiagent/SYSTEM.md`) are loaded only after you trust the
directory. TM asks on first use, remembers the decision in `<config>/trust.json`,
and honors `--approve`/`--no-approve`, `/trust [off]`, and the
`default_project_trust` setting (`ask` | `always` | `never`). Context files such
as `AGENTS.md` are not gated. Non-interactive runs (`-p`, `--json`, `--mode rpc`)
do not prompt and decline by default.

**Changes and undo.** Mutating tools append to `<config>/changes/changes.jsonl`.
File writes/edits snapshot the previous content; package installs and service
start/stops record enough to reverse them, so `/undo [n]` restores the previous
state. Destructive shell commands (a built-in deny list) always need an explicit,
non-remembered approval. `elevate` is never auto-approved or remembered. Use
`tm --dry-run` to preview mutations without applying them.

## Interactive commands

Inside `tm` (agent REPL or TUI) type `/` for commands:

| Command | Description |
|---|---|
| `/help` | list commands |
| `/model [pattern]` | list models or switch model |
| `/new` | start a new session |
| `/session` | show current session id/path |
| `/resume [n\|id]` | resume a saved session (`/resume` opens a picker) |
| `/tree [n]` | list conversation points, or branch from point n |
| `/fork [n]` | fork the session (at point n) into a new file |
| `/compact [note]` | summarize older context |
| `/recover` | reconcile interrupted durable operations |
| `/undo [n]` | roll back the last n reversible changes |
| `/copy [n]` | copy the nth-from-last reply to the clipboard |
| `/skills` | list available skills |
| `/skill:<name>` | load a skill into the conversation |
| `/prompts` | list prompt templates |
| `/<template> [args]` | expand a prompt template |
| `/exit` | quit |

**Completion.** In the TUI, press `Tab` to accept a suggestion: type `/` to
complete a command, or `@` to fuzzy-search a file path. `Up`/`Down` move the
selection and `Escape` dismisses it.

**Input.** The TUI prompt is multi-line: `Enter` submits, `Shift+Enter` (or
`Ctrl+J`) inserts a newline, and pasting keeps every line.

## Sessions

Sessions are appended to JSONL files under `<config>/sessions/`, tagged with the
working directory. `tm` **continues the most recent session for the current
directory by default** (in the TUI, its history is rendered on startup).

Each session file is a full store with three durable forms — an append-only
**entry tree**, bound **values/lists**, and an append-only **usage ledger**.
Writes commit **atomically** (one transaction per JSONL line; a torn final line
is discarded whole), and the durable operation restart point from `--durable`
lives in the same store.

- `tm` — continue the most recent session for this directory.
- `tm --new-session` — start a fresh session (also `/new` inside a session).
- `tm -c` — explicit form of the default.
- `tm -r` — list saved sessions (number, message count, updated time, preview)
  and pick one; `--all-sessions` includes other directories.
- `tm --session <file>` — open a specific file.
- `tm --no-session` — run without persisting.

Inside a session, `/resume [n|id]` switches sessions (a picker modal in the TUI),
`/tree` navigates conversation points, and `/fork` copies a branch to a new file.
The TUI re-renders the resumed conversation's history on startup and after
`/resume`, `/new`, `/tree`, and `/fork`.

The provider/model you switch to with `/model` is remembered **per session** and
restored when that session is resumed, overriding the settings default.

## Settings

`<config>/settings.toml` (Windows: `%APPDATA%\the-machine\settings.toml`):

```toml
provider = "deepseek"
model = "deepseek-v4-pro"
temperature = 0.2
max_tokens = 8192
system_prompt = "Extra instructions appended to the system prompt."
auto_compact = true      # summarize older context when nearing the limit
compact_threshold = 0.8  # fraction of the context window that triggers it
compact_keep_recent = 6  # recent messages kept verbatim
default_project_trust = "ask"  # ask | always | never
cache_retention = "short"      # short | long (provider prompt cache; TM_CACHE_RETENTION overrides)
env_probe = true               # add a machine summary to the system prompt
python_executable = ""         # interpreter for the python tool (default: the one running TM)
python_timeout = 120           # seconds per python script
workspace_dir = ""             # where python scripts are saved (default: <config>/workspace)
auto_install = true            # install missing pip packages declared by the python tool
```

Automatic compaction runs before a prompt when the estimated context exceeds
`context_window * compact_threshold`. Disable per run with `--no-auto-compact`.
If a turn ends truncated or with an overflow error, TM compacts and retries once.
Compaction is stored as a durable entry, so the summarized context survives a
restart.

Per-model pricing (`input_cost`, `output_cost`, `cache_read_cost`, USD per 1M
tokens) can be set in `models.toml`; the footer then shows `$cost` and the cache
hit rate (`CH%`).

## Machine awareness and the Python fallback

TM inspects the machine before assuming anything, and writes Python when shell
one-liners are not enough.

- **Environment probe.** At startup TM appends a compact summary of the machine
  (OS, CPU/memory/disk, GPUs, Python, installed tools) to the system prompt. The
  `system_info` tool returns the same facts on demand (pass `full` for tool
  paths). Disable with `env_probe = false`.
- **`python` tool.** Runs a Python script with a configurable interpreter
  (default: the one running TM), saves it under `<config>/workspace/scripts/` so
  it can be re-run or edited, streams output, enforces a timeout, and returns
  the traceback on failure. Declare `packages` to have missing pip dependencies
  installed first (auto-install, gated by the network permission). Disable with
  `--no-python`.
- **Solve ladder.** The system prompt tells the model to prefer, in order: an
  installed tool, the OS-native command, the Python standard library, a package,
  then a system-level change; to verify every step; and to try before asking.

The architecture and the multi-phase plan live in
[docs/design/device-management.md](docs/design/device-management.md).

**Tool output.** Command and file output is capped at 2000 lines or 50KB,
whichever is hit first. Command output (shell, python, elevate) keeps the
**end**, where errors and the exit code appear, and a non-zero exit code marks
the result as an error; file and search output keeps the **beginning** (reads add
`Use offset=N to continue`, grep caps each match line at 500 characters). When
output is truncated, the full text is saved under `<config>/workspace/spill/` and
the notice names that file.

## Customization

**Context files.** `AGENTS.md` / `CLAUDE.md` (and `AGENTS.override.md`) are
loaded from the global config dir and walking up from the working directory, and
appended to the system prompt. Disable with `--no-context-files`.

**Skills.** A skill is a directory with a `SKILL.md` (optional frontmatter with
`name` and `description`). Load from `<config>/skills/`, `.agents/skills/`, or
`.aiagent/skills/`. Their names/descriptions are advertised in the system prompt;
load one with `/skill:<name>`.

```markdown
---
name: code-review
description: Review code for bugs and security issues
---
Review the target for bugs, security issues, and missing tests.
```

**Prompt templates.** Markdown files in `<config>/prompts/` or
`.aiagent/prompts/`, invoked as `/<filename>`; `{{input}}` is replaced by the
text after the command.

```markdown
<!-- prompts/review.md -->
Review this for bugs, security, and performance: {{input}}
```

**Extensions.** Python modules in `<config>/extensions/` or
`.aiagent/extensions/`. Each defines `setup(api)` and can register tools, event
listeners, and slash commands. Extensions run with full process permissions.

```python
from tm.tools.base import Tool, text_result

def setup(api):
    api.register_tool(MyTool())
    api.on("tool_execution_end", lambda event: None)
    api.register_command("greet", lambda arg: f"hello {arg}")
```

## Telemetry

Opt in with `--telemetry` (or `telemetry = true` in settings). Spans are written
to `<config>/telemetry.jsonl`. The contract (`tm/telemetry/`) is deliberately
narrow and **never carries prompts, message content, tool arguments, tool
output, or credentials** — only structural metadata and counts. `redact()` is
the single choke point, and adapters (`NoopTelemetry`, `MemoryTelemetry`,
`FileTelemetry`) can only ever see redacted spans.

## Durable operations

Opt in with `--durable` (or `durable = true`). Each `tm` run is a durable
*operation* tracked in `<config>/state.jsonl`: before an uncertain external
effect (a tool call) the intent is recorded, and it is settled afterwards.
Messages are persisted to the session as they are produced, so the in-flight
call is durable too.

On the next start, TM **automatically recovers** interrupted operations:

- a `replay_safe` effect (read-only tools) is re-run from the recorded intent and
  the run continues;
- an effect that is not replay-safe is not re-run; it is surfaced to the model as
  an error result telling it to check the effects;
- an operation with no effect in flight is settled as aborted.

Recovered re-runs still go through the permission gate. See
`tm/core/operation.py` and `tm/core/agent.py`.

## Architecture

- `tm/ai` – unified multi-provider streaming LLM layer (types, event stream,
  OpenAI-compatible + Anthropic + Google adapters, registry/catalog)
- `tm/core` – agent loop, high-level `Agent`, events, sessions, compaction, and
  the durable `store`/`operation` restart point
- `tm/telemetry` – vendor-neutral telemetry contract, redaction, adapters
- `tm/tools` – built-in machine-control tools
  (read/write/edit/shell/python/grep/find/ls/system_info, plus process/service/
  package/elevate for lifecycle, services, packages, and guided elevation)
- `tm/host` – cross-platform machine probing and control (identity, resources,
  software, processes, services, packages, elevation)
- `tm/safety` – change journal, reversible snapshots, dangerous-command guard
- `tm/permissions` – policy, allow/deny/ask checker, approval, audit log
- `tm/skills.py`, `tm/prompts.py`, `tm/extensions.py`, `tm/context` – resources
- `tests/evals` – faux-provider eval scenarios
- `tm/tui` – terminal UI (Textual)
- `tm/cli` – Typer entry point and slash commands

## Development

Set up once (installs the project editable, so source edits take effect with no
reinstall):

```bash
python -m uv sync --all-extras
```

Run the app from source:

```bash
python -m uv run tm                # TUI
python -m uv run tm --no-tui       # console REPL
python -m uv run tm "prompt"       # one-shot agent run
python -m uv run tm --json "prompt"  # stream agent events as JSON (great for debugging)
python -m uv run tm --login deepseek # store an API key
```

On Windows, `start.bat` (cmd) and `run.ps1` (PowerShell) wrap the above and set
up dependencies on first run:

```powershell
.\start.bat            # or: .\run.ps1
.\start.bat --no-tui   # console REPL
.\start.bat "prompt"   # one-shot
```

Tests and quality gates (same as CI):

```bash
python -m uv run pytest -q                         # full suite
python -m uv run pytest tests/evals -q -m eval     # offline eval scenarios
python -m uv run python scripts/run-evals.py       # eval report
python -m uv run pytest tests/test_tui.py -q       # one file
python -m uv run pytest -q -k tool                 # by name
python -m uv run ruff check .                      # lint
python -m uv run mypy                              # types
python -m uv build                                 # wheel + sdist
```

Before a release, `scripts/release-smoke.ps1` builds, validates metadata in a
throwaway venv, and runs `tm --version` / `tm --list-models`.

**Secret scanning.** CI runs gitleaks (`.github/workflows/secret-scan.yml`,
configured by `.gitleaks.toml`), and `tests/test_no_secrets.py` fails the suite
if a tracked file looks like it contains a committed secret. Never put real keys
in tests, docs, or fixtures; use an obvious placeholder such as `sk-xxxx`.

Notes:

- The live DeepSeek test only runs when `DEEPSEEK_API_KEY` is set;
  `scripts/smoke_deepseek.py` is a standalone live smoke test.
- TUI behavior is tested headlessly with Textual's `run_test` (see
  `tests/test_tui.py`): set the `#prompt` value, press enter, wait for workers,
  then assert on `app.query(".assistant")` etc.
- Do **not** use `uv tool install` while developing: it copies the code (edits
  won't show) and can fail with a file lock if `tm` is running. To test a real
  install, use a throwaway tool dir:
  `UV_TOOL_DIR=$env:TEMP\tm-tool uv tool install ".[all]"`.

`uv.lock` is committed. CI (`.github/workflows/ci.yml`) runs ruff and mypy on
Linux, and the test suite on Linux, Windows, and macOS for Python 3.11 and 3.12.
See [RELEASE.md](RELEASE.md) for publishing to PyPI.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| P0 | scaffolding, config, CLI | done |
| P1 | ai layer + domestic providers | done |
| P2 | agent loop, tools, events | done |
| P3 | built-in tools | done |
| P4 | permission system + CLI wiring | done |
| P5 | Textual TUI | done |
| P6 | session persistence (JSONL tree) | done |
| P7 | Anthropic + Ollama providers | done |
| P8 | context files, skills, prompt templates, extensions | done |
| P9 | compaction, print/json mode, packaging | done |
| P10 | sessions: resume picker, fork, tree navigation | done |
| P11 | Google Gemini + extra providers, TUI streaming/status | done |
| P12 | device management A: host probe, python tool, solve ladder | done |
| P13 | device management B: process/service/package lifecycle | done |
| P14 | device management C: guided elevation, change journal, `/undo`, dry-run | done |

Device management phases D-G are planned in
[docs/design/device-management.md](docs/design/device-management.md): dynamic
monitoring, OS-native scheduling, remote SSH, and reuse/specialization. Possible
future work: more providers, provider-side prompt caching controls, a web UI, and
multi-agent orchestration.

## License

MIT, see [LICENSE](LICENSE).

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
tm --login deepseek        # store an API key (hidden input)
tm "list the files in this folder"
```

Requires Python 3.11+. uv installs a suitable interpreter automatically.

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

1. **Store it once (recommended)**. Prompts with hidden input and saves to the
   credentials file:

   ```powershell
   python -m uv run tm --login deepseek
   ```

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
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | deepseek-chat |
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

## Modes

| Command | Behavior |
|---|---|
| `tm` | Interactive TUI (Textual) with tools and permissions |
| `tm "prompt"` | One-shot agent run |
| `tm --no-tui` | Plain console REPL instead of the TUI |
| `tm --tui` | Force the TUI (default when interactive) |
| `tm --read-only` | Only read/grep/find/ls tools |
| `tm --no-tools` | Plain chat, no machine control |
| `tm --yolo` | Auto-approve every action |
| `tm -p` | One-shot, reads stdin when no prompt is given |
| `tm --json` | Emit agent events as JSON lines (for integration) |
| `tm -c` | Continue the most recent session in this directory |
| `tm -r` | Pick a saved session to resume (`--all-sessions` to include other dirs) |
| `tm --no-extensions` | Skip loading `.aiagent/extensions` |
| `tm --no-auto-compact` | Disable automatic context compaction |

Context files (`AGENTS.md` / `CLAUDE.md`, walking up from cwd, plus the global
config dir) are appended to the system prompt. Disable with `--no-context-files`.

When stdout is not a terminal (piped or redirected) or `textual` is not
installed, `tm` falls back to the console REPL automatically.

## Permissions

Every file, shell, and network action is evaluated against a policy. Deny rules
win, then allow rules, then the `default` decision (`ask` by default). Undecided
actions prompt the user; `a` remembers the decision for the session. All
decisions are appended to `<config>/audit.jsonl`.

Policy files are merged from `<config>/policy.toml` (global) and
`<cwd>/.aiagent/policy.toml` (project). Example:

```toml
default = "ask"

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

Note: shell commands are matched textually. TM cannot reliably stop a shell
command from making network calls; use a sandbox/container when you need a hard
network boundary.

## Interactive commands

Inside `tm` (agent REPL or TUI) type `/` for commands:

| Command | Description |
|---|---|
| `/help` | list commands |
| `/model [pattern]` | list providers or switch model |
| `/new` | start a new session |
| `/session` | show current session id/path |
| `/resume [n\|id]` | resume a saved session (`/resume` opens a picker) |
| `/tree [n]` | list conversation points, or branch from point n |
| `/fork [n]` | fork the session (at point n) into a new file |
| `/compact [note]` | summarize older context |
| `/skills` | list available skills |
| `/skill:<name>` | load a skill into the conversation |
| `/prompts` | list prompt templates |
| `/<template> [args]` | expand a prompt template |
| `/exit` | quit |

## Sessions

Sessions are appended to JSONL files under `<config>/sessions/`, tagged with the
working directory. Resume them in several ways:

- `tm -c` — continue the most recent session for this directory.
- `tm -r` — list saved sessions (number, message count, updated time, preview)
  and pick one; `--all-sessions` includes other directories.
- `tm --session <file>` — open a specific file.
- `tm --no-session` — run without persisting.

Inside a session, `/resume [n|id]` switches sessions (a picker modal in the TUI),
`/tree` navigates conversation points, and `/fork` copies a branch to a new file.

## Settings

`<config>/settings.toml` (Windows: `%APPDATA%\the-machine\settings.toml`):

```toml
provider = "deepseek"
model = "deepseek-chat"
temperature = 0.2
max_tokens = 8192
system_prompt = "Extra instructions appended to the system prompt."
auto_compact = true      # summarize older context when nearing the limit
compact_threshold = 0.8  # fraction of the context window that triggers it
compact_keep_recent = 6  # recent messages kept verbatim
```

Automatic compaction runs before a prompt when the estimated context exceeds
`context_window * compact_threshold`. Disable per run with `--no-auto-compact`.

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

## Architecture

- `tm/ai` – unified multi-provider streaming LLM layer (types, event stream,
  OpenAI-compatible + Anthropic + Google adapters, registry/catalog)
- `tm/core` – agent loop, high-level `Agent`, events, sessions, compaction
- `tm/tools` – built-in machine-control tools (read/write/edit/shell/grep/find/ls)
- `tm/permissions` – policy, allow/deny/ask checker, approval, audit log
- `tm/skills.py`, `tm/prompts.py`, `tm/extensions.py`, `tm/context` – resources
- `tm/tui` – terminal UI (Textual)
- `tm/cli` – Typer entry point and slash commands

## Development

```bash
python -m uv sync --all-extras   # install dev + provider extras
python -m uv run pytest -q       # tests
python -m uv run ruff check .    # lint
python -m uv run mypy            # types
python -m uv build               # wheel + sdist
```

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

All planned phases are implemented. Possible future work: more providers,
provider-side prompt caching controls, a web UI, and multi-agent orchestration.

## License

MIT, see [LICENSE](LICENSE).

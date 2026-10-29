# TM quick configuration (5 minutes)

Goal: install TM, configure a model key and default model, then use it
comfortably. Full settings are in [README](../README.md#settings); the tm_im
integration is in [docs/im.md](im.md).

## 1. Install

```bash
uv tool install "the-machine[all]"
tm --version
```

Without `uv`:

```bash
pip install -U "the-machine[all]"
```

`[all]` adds the Anthropic, Google, and TUI extras. For OpenAI-compatible
providers only, the base install is enough. Python 3.8+ is supported (3.11/3.12
is recommended; Python 3.8 gets an older Textual).

## 2. Store a model key

`tm --login` is the recommended path (writes `credentials.toml`, which takes
precedence over environment variables):

```bash
tm --login deepseek
tm --list-models          # a leading * means a key was found
```

Locations:

- Windows: `%APPDATA%\the-machine\credentials.toml`
- Linux/macOS: `~/.config/the-machine/credentials.toml`

Environment variables work too (current shell):

```bash
export DEEPSEEK_API_KEY=sk-...
```

Common providers:

| Provider | `tm --login` id | Environment variable |
|---|---|---|
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |
| Google Gemini | `google` | `GEMINI_API_KEY` |
| Qwen / DashScope | `qwen` | `DASHSCOPE_API_KEY` |
| Moonshot / Kimi | `moonshot` | `MOONSHOT_API_KEY` |
| Zhipu GLM | `zhipu` | `ZHIPUAI_API_KEY` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` |
| SiliconFlow | `siliconflow` | `SILICONFLOW_API_KEY` |

See the "Providers" section of the README for the full list.

## 3. Choose the default model

```bash
tm --list-models                          # list models
tm --model deepseek-v4-pro                # override for one run
tm --model deepseek/deepseek-v4-pro       # provider/model form
```

Persist it in settings:

```toml
# settings.toml
provider = "deepseek"
model = "deepseek-v4-pro"
```

Switch inside a session with `/model` (TUI or console REPL).

## 4. Recommended settings.toml

Paths:

- Windows: `%APPDATA%\the-machine\settings.toml`
- Linux/macOS: `~/.config/the-machine/settings.toml`

A good starting point (change the first two lines to your provider/model):

```toml
provider = "deepseek"
model = "deepseek-v4-pro"
temperature = 0.2
language = "auto"          # en | zh | auto

auto_compact = true
compact_max_tokens = 150000   # hard context budget; 0 = window ratio only
compact_keep_recent = 6
prune_tool_output = true
prune_tool_output_keep_turns = 8
prune_tool_output_min_chars = 2000
prune_tool_output_keep_chars = 400
read_dedupe = true

max_turns = 300
max_retries = 3
env_probe = true
cache_retention = "short"
```

Notes:

- `compact_max_tokens` is the main cost/context control. With a 1M-token model,
  leaving it at `0` lets the context grow to 80% of the window; 125k-200k is a
  practical working range.
- `prune_tool_output*` rewrites old tool output only in the request; the session
  keeps the full text. Spill files live under `<config>/workspace/spill`
  (override with `spill_dir`).
- `read_dedupe` answers a repeated read of an unchanged file/range with a short
  note instead of the same content again.
- `env_probe = false` removes the machine summary from the system prompt.
- `cache_retention = "long"` suits long sessions (Anthropic 1h cache); support
  for OpenAI-compatible gateways depends on the server.
- To avoid editing the file: run `/settings` in the TUI, or use the numbered
  editor in the plain console.

## 5. Verify

```bash
tm --list-models
tm "reply with OK only"   # one-shot check of key + model
tm                        # start the TUI
```

In the TUI:

- `/help` lists commands
- `/settings` edits configuration interactively
- `/model` switches models
- `Esc` stops a turn, `Ctrl+O` expands tool details, `Ctrl+Shift+Q` quits
- sensitive actions ask for permission; `/auto on` or `tm --yolo` auto-approves
  (use carefully)

## 6. Common setups

### Local models (Ollama / LM Studio / vLLM)

OpenAI-compatible local endpoints need no key:

```bash
tm models add local \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen2.5-coder:7b \
  --context-window 32768 \
  --max-tokens 4096 \
  --default -y

tm
```

### Custom hosted provider

```bash
tm models add myprovider \
  --base-url https://api.example.com/v1 \
  --model my-model \
  --api-key-env MYPROVIDER_API_KEY \
  --default -y

tm --login myprovider      # store the actual key
tm --model my-model
```

Manage `models.toml` with `tm models list`, `tm models edit myprovider`, and
`tm models delete myprovider`.

### TUI over SSH / Xshell

```bash
tm --terminal-info      # TERM, encoding, size, color system
tm --tui-inline         # when full-screen redraws are mangled
tm --no-tui             # plain console fallback
```

tm steps `TERM=xterm` up to 256 colors automatically. Force it with
`TM_TUI_COLOR_SYSTEM=16|256|truecolor`. For Xshell, use UTF-8 and a monospace
CJK font.

### Project context and skills

- `AGENTS.md` / `CLAUDE.md` are loaded from the current directory upward.
- Global and project skills: `<config>/skills`, `.agents/skills`,
  `.aiagent/skills`.
- Prompt templates: `<config>/prompts`, `.aiagent/prompts`; invoke with
  `/template-name`.

### `tm im` (chat network)

`tm im` has two keys: the model key is shared with `tm`; the `api_key`
(`sk_live_...`) in `im.toml` is the tm_im server credential, not a model key.

```bash
tm --login deepseek                                      # 1) model key
tm im config new alice -p deepseek -m deepseek-v4-pro    # 2) tm_im key + model
# or, for an existing config, choose everything interactively:
tm im config model setup --config alice
tm im start --config alice
tm im logs -f --config alice
tm im status --config alice
```

Restart after changing keys:

```bash
tm im stop --config alice && tm im start --config alice
```

Full reference: `docs/im.md`.

## 7. Config file map

| Path | Contents |
|---|---|
| `<config>/settings.toml` | Default provider/model, compaction, pruning, language, TUI |
| `<config>/credentials.toml` | Provider API keys (`tm --login`) |
| `<config>/models.toml` | Custom providers/models (`tm models`) |
| `<config>/sessions/` | Saved sessions |
| `<config>/approvals.json` | Remembered permission approvals |
| `<config>/audit.jsonl` | Permission audit |
| `<config>/changes/` | Change journal and spill output |
| `<config>/im.toml`, `<config>/configs/<name>.toml` | tm_im configuration |
| `<config>/im/`, `<config>/im/instances/.../` | tm_im state, logs, sessions |

`<config>` can be overridden with `TM_CONFIG_DIR`. To keep fully separate setups
(such as different model keys), use a different `TM_CONFIG_DIR`.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `no API key` / auth failure | `tm --login <provider>`, then check `tm --list-models` for `*` |
| Model 404 / unknown model | Pick the exact id from `tm --list-models`; use `tm models` for custom models |
| Context too long / high cost | Lower `compact_max_tokens`; keep `prune_tool_output = true` |
| TUI garbled or text covered | `tm --terminal-info`; try `tm --tui-inline` or `tm --no-tui` |
| Too many permission prompts | `/auto on` or `tm --yolo` (trusted environments only) |
| TUI issues on Python 3.8 | Upgrade to Python 3.11/3.12 and reinstall (newer TUI deps) |
| `tm im` connects but never replies | Check friendship, `respond_in_groups`, `allow_tools`; read `tm im logs` |

## 9. Next steps

- Full settings: README "Settings"
- Tools and permissions: README "Permissions"
- Sessions, branches, recovery: `/help` in the TUI
- Extensions and MCP: README "Extensions"

[English](README.md) | [简体中文](README.zh-CN.md)

# The Machine (TM)

[![PyPI](https://img.shields.io/pypi/v/the-machine.svg)](https://pypi.org/project/the-machine/)
[![Python versions](https://img.shields.io/pypi/pyversions/the-machine.svg)](https://pypi.org/project/the-machine/)
[![CI](https://github.com/beyondalbert/tm/actions/workflows/ci.yml/badge.svg)](https://github.com/beyondalbert/tm/actions/workflows/ci.yml)

一个可以控制你本机、可扩展且带权限审批的本地 AI Agent。

TM 是一个用 Python 编写的 Agent 框架，架构借鉴自 [pi](../pi)，并围绕文件系统、
Shell 与网络访问的**显式权限审批**重新构建。

## 状态

核心 Agent、多 Provider、内置工具、权限系统与 CLI 均已可用。详见文末阶段表。

## 安装

`the-machine` 已发布到 [PyPI](https://pypi.org/project/the-machine/)。推荐使用
[uv](https://docs.astral.sh/uv/)（它也能顺带帮你安装 Python）。

先安装 uv（仅一次）：

```powershell
# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 安装 `tm` 命令（推荐）

```bash
uv tool install "the-machine[all]"
uv tool update-shell   # 将工具目录加入 PATH，之后重开终端
tm --help
```

或在虚拟环境中用 pip：

```bash
pip install "the-machine[all]"
```

`[all]` 会安装 Anthropic、Google 与 TUI 的可选依赖。若做最小安装可去掉它（此时仅支持
OpenAI 兼容 Provider；`--tui` 还需额外安装 `textual`）。

### 从源码安装

```bash
uv tool install "the-machine[all] @ git+https://github.com/beyondalbert/tm"
# 没有 git 时：
uv tool install "the-machine[all] @ https://github.com/beyondalbert/tm/archive/refs/heads/main.tar.gz"
```

### 从克隆仓库安装（开发）

```bash
git clone https://github.com/beyondalbert/tm
cd tm
uv sync --all-extras
uv run tm --help
```

### 首次运行

```bash
tm --login deepseek        # 保存 API Key（输入可见）
tm "list the files in this folder"
```

需要 Python 3.11+，uv 会自动安装合适的解释器。

## Provider

多 Provider 通过官方 SDK 适配。优先支持国内厂商（DeepSeek、Qwen、Kimi、智谱 GLM），
其次是 OpenAI 以及任意 OpenAI 兼容端点（Ollama、vLLM、LM Studio）。

设置好对应 Provider 的 API Key 后运行：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
python -m uv run tm "list the files in this folder"
```

### API Key 设置在哪里

三种方式，按便捷程度排序：

1. **存一次（推荐）**。输入时可见（便于确认），保存到凭据文件：

   ```powershell
   python -m uv run tm --login deepseek
   ```

   保存后会回显一个脱敏预览（例如 `sk-000...0000 (35 chars)`），便于确认是否输入正确。

   保存位置为 `<config>/credentials.toml`：
   - Windows：`%APPDATA%\the-machine\credentials.toml`
   - Linux/macOS：`~/.config/the-machine/credentials.toml`

   文件内容形如：

   ```toml
   [providers]
   deepseek = "sk-..."
   qwen = "sk-..."
   ```

2. **环境变量**（当前会话或永久）：

   ```powershell
   # 仅当前终端
   $env:DEEPSEEK_API_KEY = "sk-..."
   # 永久（之后需重开终端）
   [Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-...", "User")
   ```

3. **自定义配置目录**：设置 `TM_CONFIG_DIR` 可覆盖默认目录。

保存的凭据优先于环境变量。

Provider 与对应环境变量：

| Provider | Id | 环境变量 | 默认模型 |
|---|---|---|---|
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | deepseek-v4-pro |
| Qwen（DashScope） | `qwen` | `DASHSCOPE_API_KEY` | qwen-plus |
| Moonshot（Kimi） | `moonshot` | `MOONSHOT_API_KEY` | moonshot-v1-32k |
| Zhipu（GLM） | `zhipu` | `ZHIPUAI_API_KEY` | glm-4-plus |
| SiliconFlow | `siliconflow` | `SILICONFLOW_API_KEY` | DeepSeek-V3 |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | claude-3-5-sonnet-latest |
| Google Gemini | `google` | `GEMINI_API_KEY` | gemini-2.0-flash |
| OpenAI | `openai` | `OPENAI_API_KEY` | gpt-4o |
| Groq | `groq` | `GROQ_API_KEY` | llama-3.3-70b-versatile |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | claude-3.5-sonnet |
| Together AI | `together` | `TOGETHER_API_KEY` | Llama-3.3-70B |
| xAI | `xai` | `XAI_API_KEY` | grok-2-latest |
| Ollama（本地） | `ollama` | – | qwen2.5:7b |

Anthropic 与 Google 使用官方 SDK；其余使用 OpenAI 兼容适配器。`ollama` 等本地服务
无需 Key。

列出模型：`tm --list-models`。

### 自定义 Provider 与模型

在 `<config>/models.toml`（Windows：`%APPDATA%\the-machine\models.toml`）里定义自己的
provider（任意 OpenAI 兼容、Anthropic 或 Google 端点）或额外模型。它会合并到内置目录之上，
`tm --list-models` 与 `/model` 会直接生效，无需改代码。

```toml
[providers.myprovider]
name = "My Provider"
api = "openai-completions"          # 或 anthropic-messages / google-generative-ai
base_url = "https://api.example.com/v1"
api_key_env = "MYPROVIDER_API_KEY"  # 名字，或名字列表
default_model = "my-model"

[[providers.myprovider.models]]
id = "my-model"
context_window = 32768
max_tokens = 4096

# 给内置 provider 追加或覆盖模型
[[providers.deepseek.models]]
id = "deepseek-v4-lite"
context_window = 65536
```

用 `tm --login myprovider` 保存密钥，或设置上面命名的环境变量。
用 `/model my-model` 或 `tm --model my-model` 切换到自定义模型。

## 运行模式

| 命令 | 行为 |
|---|---|
| `tm` | 交互式 TUI（Textual），带工具与权限 |
| `tm "prompt"` | 一次性执行 |
| `tm --no-tui` | 使用普通控制台 REPL（不用 TUI） |
| `tm --tui` | 强制使用 TUI（交互时默认） |
| `tm --read-only` | 仅启用 read/grep/find/ls 工具 |
| `tm --no-tools` | 纯对话，不控制本机 |
| `tm --yolo` | 自动批准所有操作 |
| `tm -p` | 一次性，无提示时读取 stdin |
| `tm --json` | 以 JSON Lines 输出 Agent 事件（便于集成） |
| `tm -c` | 继续本目录最近的会话（默认行为） |
| `tm --new-session` | 新建会话，而不是继续最近的会话 |
| `tm -r` | 选择已保存的会话恢复（`--all-sessions` 含其它目录） |
| `tm --no-extensions` | 不加载 `.aiagent/extensions` |
| `tm --no-auto-compact` | 关闭自动上下文压缩 |
| `tm --telemetry` | 将脱敏 span 写入 `<config>/telemetry.jsonl` |
| `tm --durable` | 为每次运行在 `<config>/state.jsonl` 持久化重启点 |
| `tm --no-mouse` | 让终端接管鼠标选择/复制 |

上下文文件（`AGENTS.md` / `CLAUDE.md`，从当前目录向上查找，外加全局配置目录）会追加到
系统提示词。用 `--no-context-files` 关闭。

当 stdout 不是终端（被管道/重定向）或未安装 `textual` 时，`tm` 会自动回退为控制台 REPL。

**复制文本。** TUI 会捕获鼠标以便内置选择与滚动。按住拖动选中文字后按 `Ctrl+C`（或
`Ctrl+Shift+C`）复制；Textual 通过 OSC52 写入剪贴板，多数终端支持。如果没有效果，用
`--no-mouse` 启动 TUI，改用终端自带的选择（多数终端为 Shift+拖动）。可在 settings 里设
`mouse = false` 使其成为默认。

## 权限

每一项文件、Shell、网络操作都会依据策略评估。拒绝规则优先，其次允许规则，最后是
`default` 决策（默认 `ask`）。未决操作会询问用户；输入 `a` 会在本次会话内记住该决定。
所有决策写入 `<config>/audit.jsonl`。

策略文件会合并 `<config>/policy.toml`（全局）与 `<cwd>/.aiagent/policy.toml`（项目）。
示例：

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

文件规则是**目录级**的：规则里写目录（`src`、`src/` 或 `src/**`）会覆盖其下所有内容，
可以按文件夹控制而不是逐个文件。当策略未决（`ask`）而你回答 `a`（always）时，TM 记住的是
**作用域**：文件操作记住所在文件夹，命令/网络记住具体命令/主机。

注意：Shell 命令是**按文本匹配**的。TM 无法可靠阻止某条 Shell 命令发起网络请求；
若需要硬性网络边界，请使用沙箱/容器。

## 交互命令

在 `tm`（Agent REPL 或 TUI）内输入 `/` 触发命令：

| 命令 | 说明 |
|---|---|
| `/help` | 列出命令 |
| `/model [pattern]` | 列出模型或切换模型 |
| `/new` | 新建会话 |
| `/session` | 显示当前会话 id/路径 |
| `/resume [n\|id]` | 恢复已保存会话（不带参数弹出选择器） |
| `/tree [n]` | 列出对话节点，或从第 n 个节点分支 |
| `/fork [n]` | 将该会话（第 n 个节点）分叉为新文件 |
| `/compact [note]` | 摘要较早的上下文 |
| `/recover` | 恢复被中断的持久化操作 |
| `/skills` | 列出可用技能 |
| `/skill:<name>` | 将技能载入对话 |
| `/prompts` | 列出提示词模板 |
| `/<template> [args]` | 展开提示词模板 |
| `/exit` | 退出 |

## 会话

会话以 JSONL 追加写入 `<config>/sessions/`，并标注工作目录。`tm` **默认继续本目录最近的
会话**（TUI 启动时会把历史渲染出来）。

每个会话文件就是一个完整的存储，包含三种持久形态——只追加的**条目树**、绑定地址的
**值/列表**、只追加的**使用量账本**。写入是**原子提交**的（JSONL 每行一个事务；末尾被截断
的行整行丢弃），`--durable` 的持久化重启点也存在同一个存储里。

- `tm` —— 继续本目录最近的会话。
- `tm --new-session` —— 新建会话（会话内也可用 `/new`）。
- `tm -c` —— 默认行为的显式写法。
- `tm -r` —— 列出已保存会话（序号、消息数、更新时间、预览）并选择；
  `--all-sessions` 含其它目录。
- `tm --session <file>` —— 打开指定文件。
- `tm --no-session` —— 不持久化运行。

会话内可用 `/resume [n|id]` 切换会话（TUI 中为选择弹窗）、`/tree` 导航对话节点、
`/fork` 将分支复制为新文件。TUI 会在启动时以及 `/resume`、`/new`、`/tree`、`/fork`
之后重新渲染恢复出来的历史消息。

## 设置

`<config>/settings.toml`（Windows：`%APPDATA%\the-machine\settings.toml`）：

```toml
provider = "deepseek"
model = "deepseek-v4-pro"
temperature = 0.2
max_tokens = 8192
system_prompt = "Extra instructions appended to the system prompt."
auto_compact = true      # summarize older context when nearing the limit
compact_threshold = 0.8  # fraction of the context window that triggers it
compact_keep_recent = 6  # recent messages kept verbatim
```

当估算上下文超过 `context_window * compact_threshold` 时，会在发送提示词前自动压缩。
单次运行可用 `--no-auto-compact` 关闭。

## 定制

**上下文文件。** 从全局配置目录及当前工作目录向上查找 `AGENTS.md` / `CLAUDE.md`
（以及 `AGENTS.override.md`），追加到系统提示词。用 `--no-context-files` 关闭。

**技能（Skills）。** 一个技能是包含 `SKILL.md` 的目录（可选 frontmatter，含 `name` 与
`description`）。加载位置：`<config>/skills/`、`.agents/skills/`、`.aiagent/skills/`。
其名称/描述会写入系统提示词；用 `/skill:<name>` 载入。

```markdown
---
name: code-review
description: Review code for bugs and security issues
---
Review the target for bugs, security issues, and missing tests.
```

**提示词模板。** 位于 `<config>/prompts/` 或 `.aiagent/prompts/` 的 Markdown 文件，
以 `/<文件名>` 调用；`{{input}}` 会被命令后的文本替换。

```markdown
<!-- prompts/review.md -->
Review this for bugs, security, and performance: {{input}}
```

**扩展（Extensions）。** 位于 `<config>/extensions/` 或 `.aiagent/extensions/` 的
Python 模块。每个模块定义 `setup(api)`，可注册工具、事件监听器与斜杠命令。
扩展以完整的进程权限运行。

```python
from tm.tools.base import Tool, text_result

def setup(api):
    api.register_tool(MyTool())
    api.on("tool_execution_end", lambda event: None)
    api.register_command("greet", lambda arg: f"hello {arg}")
```

## 遥测（Telemetry）

用 `--telemetry`（或 settings 里 `telemetry = true`）开启，span 写入
`<config>/telemetry.jsonl`。契约（`tm/telemetry/`）刻意收窄，**绝不携带提示词、消息内容、
工具参数、工具输出或凭据**——只有结构化元数据与计数。`redact()` 是唯一出口，适配器
（`NoopTelemetry`、`MemoryTelemetry`、`FileTelemetry`）只能看到已脱敏的 span。

## 持久化操作（Durable operations）

用 `--durable`（或 `durable = true`）开启。每次 `tm` 运行是一个持久化 *operation*，记录在
`<config>/state.jsonl`：在不确定的外部副作用（工具调用）之前先记录 intent，完成后再 settle。
消息在产生时就写入会话，所以进行中的调用也是持久的。

下次启动时，TM 会**自动恢复**被中断的 operation：

- `replay_safe` 的副作用（只读工具）会按记录重新执行，然后继续该次运行；
- 非 replay-safe 的副作用**不会**重跑，而是以错误结果告知模型去核对影响；
- 没有副作用在途的 operation 会被 settle 为 aborted。

恢复时的重跑同样会经过权限门。见 `tm/core/operation.py`、`tm/core/agent.py`。

## 架构

- `tm/ai` —— 统一的多 Provider 流式 LLM 层（类型、事件流、OpenAI 兼容 +
  Anthropic + Google 适配器、注册表/目录）
- `tm/core` —— Agent 循环、高层 `Agent`、事件、会话、压缩，以及持久化的
  `store`/`operation` 重启点
- `tm/telemetry` —— 厂商无关的遥测契约、脱敏、适配器
- `tm/tools` —— 内置本机控制工具（read/write/edit/shell/grep/find/ls）
- `tm/permissions` —— 策略、allow/deny/ask 判定、审批、审计日志
- `tm/skills.py`、`tm/prompts.py`、`tm/extensions.py`、`tm/context` —— 资源
- `tests/evals` —— faux provider 评测场景
- `tm/tui` —— 终端界面（Textual）
- `tm/cli` —— Typer 入口与斜杠命令

## 开发

一次性初始化（以可编辑模式安装，改源码无需重装）：

```bash
python -m uv sync --all-extras
```

从源码运行：

```bash
python -m uv run tm                  # TUI
python -m uv run tm --no-tui         # 控制台 REPL
python -m uv run tm "prompt"         # 一次性执行
python -m uv run tm --json "prompt"  # 以 JSON 输出 agent 事件（调试利器）
python -m uv run tm --login deepseek # 保存 API Key
```

Windows 下可用 `start.bat`（cmd）或 `run.ps1`（PowerShell），首次运行会自动安装依赖：

```powershell
.\start.bat            # 或：.\run.ps1
.\start.bat --no-tui   # 控制台 REPL
.\start.bat "prompt"   # 一次性执行
```

测试与质量门（与 CI 一致）：

```bash
python -m uv run pytest -q                         # 全量
python -m uv run pytest tests/evals -q -m eval     # 离线评测场景
python -m uv run python scripts/run-evals.py       # 评测报告
python -m uv run pytest tests/test_tui.py -q       # 单文件
python -m uv run pytest -q -k tool                 # 按名称
python -m uv run ruff check .                      # lint
python -m uv run mypy                              # 类型
python -m uv build                                 # wheel + sdist
```

发布前用 `scripts/release-smoke.ps1` 构建、在临时 venv 校验元数据并运行
`tm --version` / `tm --list-models`。

说明：

- 需设置 `DEEPSEEK_API_KEY` 才会跑联网用例；`scripts/smoke_deepseek.py` 是独立的联网冒烟脚本。
- TUI 用 Textual 的 `run_test` 做无头测试（见 `tests/test_tui.py`）：设置 `#prompt` 值、
  回车、等待 worker 完成，再断言 `app.query(".assistant")` 等。
- 开发时**不要**用 `uv tool install`：它复制代码（改动不生效），且 `tm` 运行时可能因文件
  被占用而失败。要测真实安装，用临时目录：
  `UV_TOOL_DIR=$env:TEMP\tm-tool uv tool install ".[all]"`。

`uv.lock` 已提交。CI（`.github/workflows/ci.yml`）在 Linux 上运行 ruff 与 mypy，并在
Linux、Windows、macOS 上以 Python 3.11 与 3.12 运行测试。发布到 PyPI 见
[RELEASE.md](RELEASE.md)。

## 路线图

| 阶段 | 范围 | 状态 |
|---|---|---|
| P0 | 脚手架、配置、CLI | 完成 |
| P1 | ai 层 + 国内 Provider | 完成 |
| P2 | Agent 循环、工具、事件 | 完成 |
| P3 | 内置工具 | 完成 |
| P4 | 权限系统 + CLI 接入 | 完成 |
| P5 | Textual TUI | 完成 |
| P6 | 会话持久化（JSONL 树） | 完成 |
| P7 | Anthropic + Ollama Provider | 完成 |
| P8 | 上下文文件、技能、提示词模板、扩展 | 完成 |
| P9 | 压缩、print/json 模式、打包 | 完成 |
| P10 | 会话：恢复选择器、分叉、树导航 | 完成 |
| P11 | Google Gemini + 更多 Provider、TUI 流式/状态 | 完成 |

计划内的全部阶段均已实现。后续可选：更多 Provider、Provider 侧提示词缓存控制、
Web UI、多 Agent 编排。

## 许可

MIT，见 [LICENSE](LICENSE)。

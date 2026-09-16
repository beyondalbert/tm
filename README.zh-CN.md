[English](README.md) | [简体中文](README.zh-CN.md)

# The Machine (TM)

一个可以控制你本机、可扩展且带权限审批的本地 AI Agent。

TM 是一个用 Python 编写的 Agent 框架，架构借鉴自 [pi](../pi)，并围绕文件系统、
Shell 与网络访问的**显式权限审批**重新构建。

## 状态

核心 Agent、多 Provider、内置工具、权限系统与 CLI 均已可用。详见文末阶段表。

## 安装

TM 尚未发布到 PyPI，需从仓库安装。需要 [uv](https://docs.astral.sh/uv/)（它也能顺带
帮你安装 Python）。发布之后，安装命令就是 `uv tool install "the-machine[all]"`。

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
uv tool install "the-machine[all] @ git+https://github.com/beyondalbert/tm"
uv tool update-shell   # 将工具目录加入 PATH，之后重开终端
tm --help
```

`[all]` 会安装 Anthropic、Google 与 TUI 的可选依赖。若做最小安装可去掉它（此时仅支持
OpenAI 兼容 Provider；`--tui` 还需额外安装 `textual`）。使用 `git+https` 源需要先装好
`git`；若没有 `git`，可改用源码压缩包安装：

```bash
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
tm --login deepseek        # 保存 API Key（隐藏输入）
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

1. **存一次（推荐）**。隐藏输入并保存到凭据文件：

   ```powershell
   python -m uv run tm --login deepseek
   ```

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
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | deepseek-chat |
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

## 运行模式

| 命令 | 行为 |
|---|---|
| `tm` | 带工具与权限的交互式 Agent REPL |
| `tm "prompt"` | 一次性执行 |
| `tm --tui` | Textual TUI，含审批弹窗 |
| `tm --read-only` | 仅启用 read/grep/find/ls 工具 |
| `tm --no-tools` | 纯对话，不控制本机 |
| `tm --yolo` | 自动批准所有操作 |
| `tm -p` | 一次性，无提示时读取 stdin |
| `tm --json` | 以 JSON Lines 输出 Agent 事件（便于集成） |
| `tm -c` | 继续本目录最近的会话 |
| `tm -r` | 选择已保存的会话恢复（`--all-sessions` 含其它目录） |
| `tm --no-extensions` | 不加载 `.aiagent/extensions` |
| `tm --no-auto-compact` | 关闭自动上下文压缩 |

上下文文件（`AGENTS.md` / `CLAUDE.md`，从当前目录向上查找，外加全局配置目录）会追加到
系统提示词。用 `--no-context-files` 关闭。

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

注意：Shell 命令是**按文本匹配**的。TM 无法可靠阻止某条 Shell 命令发起网络请求；
若需要硬性网络边界，请使用沙箱/容器。

## 交互命令

在 `tm`（Agent REPL 或 TUI）内输入 `/` 触发命令：

| 命令 | 说明 |
|---|---|
| `/help` | 列出命令 |
| `/model [pattern]` | 列出 Provider 或切换模型 |
| `/new` | 新建会话 |
| `/session` | 显示当前会话 id/路径 |
| `/resume [n\|id]` | 恢复已保存会话（不带参数弹出选择器） |
| `/tree [n]` | 列出对话节点，或从第 n 个节点分支 |
| `/fork [n]` | 将该会话（第 n 个节点）分叉为新文件 |
| `/compact [note]` | 摘要较早的上下文 |
| `/skills` | 列出可用技能 |
| `/skill:<name>` | 将技能载入对话 |
| `/prompts` | 列出提示词模板 |
| `/<template> [args]` | 展开提示词模板 |
| `/exit` | 退出 |

## 会话

会话以 JSONL 追加写入 `<config>/sessions/`，并标注工作目录。恢复方式：

- `tm -c` —— 继续本目录最近的会话。
- `tm -r` —— 列出已保存会话（序号、消息数、更新时间、预览）并选择；
  `--all-sessions` 含其它目录。
- `tm --session <file>` —— 打开指定文件。
- `tm --no-session` —— 不持久化运行。

会话内可用 `/resume [n|id]` 切换会话（TUI 中为选择弹窗）、`/tree` 导航对话节点、
`/fork` 将分支复制为新文件。

## 设置

`<config>/settings.toml`（Windows：`%APPDATA%\the-machine\settings.toml`）：

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

## 架构

- `tm/ai` —— 统一的多 Provider 流式 LLM 层（类型、事件流、OpenAI 兼容 +
  Anthropic + Google 适配器、注册表/目录）
- `tm/core` —— Agent 循环、高层 `Agent`、事件、会话、压缩
- `tm/tools` —— 内置本机控制工具（read/write/edit/shell/grep/find/ls）
- `tm/permissions` —— 策略、allow/deny/ask 判定、审批、审计日志
- `tm/skills.py`、`tm/prompts.py`、`tm/extensions.py`、`tm/context` —— 资源
- `tm/tui` —— 终端界面（Textual）
- `tm/cli` —— Typer 入口与斜杠命令

## 开发

```bash
python -m uv sync --all-extras   # install dev + provider extras
python -m uv run pytest -q       # tests
python -m uv run ruff check .    # lint
python -m uv run mypy            # types
python -m uv build               # wheel + sdist
```

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

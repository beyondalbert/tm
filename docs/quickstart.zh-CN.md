# TM 快速配置（5 分钟）

目标：装好 TM、配好模型 key、选好默认模型，然后能稳定地用起来。
完整设置项见 [README](../README.zh-CN.md#设置)，`tm im` 见
[docs/im.zh-CN.md](im.zh-CN.md)。

## 1. 安装

```bash
uv tool install "the-machine[all]"
tm --version
```

没有 `uv` 也可以：

```bash
pip install -U "the-machine[all]"
```

`[all]` 包含 Anthropic / Google 和 TUI 依赖；只用 OpenAI 兼容接口时可以不装。
要求 Python 3.8+（建议 3.11/3.12；Python 3.8 只能用到较旧的 Textual）。

## 2. 配一个大模型 key

推荐用 `tm --login`（输入后保存到 `credentials.toml`，优先级高于环境变量）：

```bash
tm --login deepseek
tm --list-models          # 行首带 * 的 provider 表示已找到 key
```

保存位置：

- Windows：`%APPDATA%\the-machine\credentials.toml`
- Linux/macOS：`~/.config/the-machine/credentials.toml`

等价的环境变量方式（当前 shell）：

```bash
export DEEPSEEK_API_KEY=sk-...
```

常用 provider 的 key 环境变量：

| Provider | `tm --login` 的 id | 环境变量 |
|---|---|---|
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |
| Google Gemini | `google` | `GEMINI_API_KEY` |
| Qwen / DashScope | `qwen` | `DASHSCOPE_API_KEY` |
| Moonshot / Kimi | `moonshot` | `MOONSHOT_API_KEY` |
| 智谱 GLM | `zhipu` | `ZHIPUAI_API_KEY` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` |
| SiliconFlow | `siliconflow` | `SILICONFLOW_API_KEY` |

其它 provider 与完整列表见 README 的 “Providers” 一节。

## 3. 选默认模型

```bash
tm --list-models                          # 查看可用模型
tm --model deepseek-v4-pro                # 单次覆盖
tm --model deepseek/deepseek-v4-pro       # provider/model 形式
```

持久化到设置：

```toml
# settings.toml
provider = "deepseek"
model = "deepseek-v4-pro"
```

会话内切换：`/model`（TUI 或控制台 REPL）。

## 4. 推荐 settings.toml

路径：

- Windows：`%APPDATA%\the-machine\settings.toml`
- Linux/macOS：`~/.config/the-machine/settings.toml`

一个可直接使用的起点（按你的 provider/模型改前两行）：

```toml
provider = "deepseek"
model = "deepseek-v4-pro"
temperature = 0.2
language = "auto"          # en | zh | auto

auto_compact = true
compact_max_tokens = 150000   # 上下文预算硬上限；0 = 只用窗口比例
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

说明：

- `compact_max_tokens` 是控制成本和上下文长度的主要开关。对于 1M 窗口模型，
  不设上限会一直涨到窗口的 80%；125k–200k 是常用的工作区间。
- `prune_tool_output*` 只在发给模型的请求里改写旧工具输出，会话文件仍保留原文；
  完整内容在 `<config>/workspace/spill`（可用 `spill_dir` 改）。
- `read_dedupe` 让重复读取未变化的文件/行范围只回一句提示。
- `env_probe = false` 可去掉系统提示词里的本机环境摘要。
- `cache_retention = "long"` 适合长会话（Anthropic 1h 缓存）；OpenAI 兼容网关
  是否支持取决于服务端。
- 不想手改文件：TUI 里运行 `/settings`，或控制台里用编号方式修改。

## 5. 验证

```bash
tm --list-models
tm "只回复 OK"        # 一次性调用，验证 key + 模型
tm                     # 进入 TUI
```

TUI 里：

- `/help` 查看命令
- `/settings` 交互修改配置
- `/model` 切换模型
- `Esc` 停止当前回合，`Ctrl+O` 展开工具详情，`Ctrl+Shift+Q` 退出
- 首次执行敏感操作会询问权限；`/auto on` 或 `tm --yolo` 可自动放行（谨慎）

## 6. 常用场景

### 本地模型（Ollama / LM Studio / vLLM）

OpenAI 兼容端点不需要 key：

```bash
tm models add local \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen2.5-coder:7b \
  --context-window 32768 \
  --max-tokens 4096 \
  --default -y

tm
```

### 自定义云端 provider

```bash
tm models add myprovider \
  --base-url https://api.example.com/v1 \
  --model my-model \
  --api-key-env MYPROVIDER_API_KEY \
  --default -y

tm --login myprovider      # 存实际 key
tm --model my-model
```

`tm models list` / `tm models edit myprovider` / `tm models delete myprovider`
可管理 `models.toml`。

### 通过 SSH / Xshell 使用 TUI

```bash
tm --terminal-info      # 检查 TERM、编码、尺寸、色彩系统
tm --tui-inline         # 全屏重绘异常时
tm --no-tui             # 纯控制台兜底
```

tm 会自动把 `TERM=xterm` 提升为 256 色；需要强制时用
`TM_TUI_COLOR_SYSTEM=16|256|truecolor`。Xshell 建议 UTF-8 编码、等宽中文字体。

### 项目约定与技能

- `AGENTS.md` / `CLAUDE.md` 会从当前目录向上查找并加入提示词。
- 全局与项目技能：`<config>/skills`、`.agents/skills`、`.aiagent/skills`。
- 提示词模板：`<config>/prompts`、`.aiagent/prompts`，用 `/模板名` 调用。

### `tm im`（接入聊天网络）

`tm im` 有**两套 key**：模型 key 和 `tm` 共用；`im.toml` 里的 `api_key`
（`sk_live_...`）是 tm_im 服务器凭证，不是模型 key。

```bash
tm --login deepseek                              # 1) 模型 key（与 tm 共用）
tm im config new alice -p deepseek -m deepseek-v4-pro   # 2) tm_im 凭证 + 模型
# 已存在的配置也可以交互式选择 provider/model 并补 key：
tm im config setup --config alice
tm im start --config alice
tm im logs -f --config alice
tm im status --config alice
```

重启后生效：

```bash
tm im stop --config alice && tm im start --config alice
```

完整说明：`docs/im.zh-CN.md`。

## 7. 配置文件一览

| 路径 | 内容 |
|---|---|
| `<config>/settings.toml` | 默认 provider/model、压缩、裁剪、语言、TUI 等 |
| `<config>/credentials.toml` | 各 provider 的 API key（`tm --login`） |
| `<config>/models.toml` | 自定义 provider / 模型（`tm models`） |
| `<config>/sessions/` | 会话记录 |
| `<config>/approvals.json` | 记住的权限授权 |
| `<config>/audit.jsonl` | 权限审计 |
| `<config>/changes/` | 变更日志与 spill 输出 |
| `<config>/im.toml`、`<config>/configs/<name>.toml` | tm_im 配置 |
| `<config>/im/` 或 `<config>/im/instances/.../` | tm_im 实例状态、日志、会话 |

`<config>` 可用 `TM_CONFIG_DIR` 覆盖。多套完全隔离的配置（例如不同模型的
key）可以用不同的 `TM_CONFIG_DIR`。

## 8. 常见问题

| 现象 | 处理 |
|---|---|
| `no API key` / 鉴权失败 | `tm --login <provider>`，再 `tm --list-models` 确认 `*` |
| 模型 404 / unknown model | `tm --list-models` 选准确 id；自定义模型用 `tm --model` 或 `tm models` |
| 上下文太长 / 费用高 | 降低 `compact_max_tokens`，保留 `prune_tool_output = true` |
| TUI 花屏、文字被遮住 | `tm --terminal-info`；试 `tm --tui-inline` 或 `tm --no-tui` |
| 权限提示太多 | `/auto on` 或 `tm --yolo`（仅可信环境） |
| Python 3.8 上 TUI 异常 | 升级到 Python 3.11/3.12 后重装（TUI 依赖会更新） |
| `tm im` 连上了但没回复 | 检查好友关系、`respond_in_groups`、`allow_tools`；看 `tm im logs` |

## 9. 下一步

- 完整设置项：README 的 “Settings”
- 工具与权限：README 的 “Permissions”
- 会话/分支/恢复：TUI 中 `/help`
- 扩展与 MCP：README 的 “Extensions”

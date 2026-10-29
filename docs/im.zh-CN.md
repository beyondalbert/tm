# tm_im 聊天网络接入

TM 可以作为 agent 接入 [tm_im](https://github.com/beyondalbert/tm_im) 服务器：
实时接收聊天消息，并通过运行正常的 TM agent（与 `tm` CLI 相同的工具、权限、
模型）进行回复。

```
tm im config new alice --model deepseek-chat   # 每个账号一个配置文件
tm im start --config alice                     # 后台运行
tm im logs -f --config alice                   # 查看日志
tm im stop  --config alice                     # 停止
```

所有子命令见 `tm im --help`。

## 快速开始

五分钟从零到能回复的 bot。下面以命名配置 `alice` 为例；省略 `--config alice`
则使用默认的 `im.toml`。

**1. 创建专用 Agent 账号。** 在 tm_im 客户端里创建一个 Agent 账号，复制它的
`api_key`（`sk_live_...`）。不要复用你还会在别处登录的人类账号：tm_im 每个账号
只允许一条连接，会把 bot 踢下线。

**2. 保存凭证。** 二选一：默认配置或命名配置。

```
tm im login                     # 默认配置（im.toml）
tm im config new alice          # 命名配置（configs/alice.toml），提示输入 key
```

需要同时运行多个账号时用命名配置。公共服务的长连接默认为
`ws://tmim.stkg.ltd/ws`，无需额外配置。如果服务器不是默认地址：

```
tm im config set --config alice base_url=https://tmim.example.com/v1 ws_url=tmim.example.com/ws
```

**3. 选择回复模型。** 详见 [快速配置](quickstart.zh-CN.md)。
`tm --list-models` 中带 `*` 的 provider 已存 key，用
`tm --login <provider>` 存 key。内置默认为 `deepseek` / `deepseek-v4-pro`。
想交互式选择并登录某个配置，运行 `tm im config model setup --config alice`：
它会把模型 key 存到共享 credentials，并把 `provider`/`model` 写进该配置；
已存过 key 时加 `--rekey` 可重新输入并覆盖；此时输入可见，保存后也会回显完整 key 供核对。

```
tm im config set --config alice provider=deepseek model=deepseek-chat
```

**4. 收紧工具权限。** `allow_tools=full`（默认）意味着任何能给 bot 发消息的人
都能在本机执行 shell 命令。

```
tm im config set --config alice allow_tools=read_only   # none | read_only | full
```

**5. 启动并确认。** 启动正常时日志会打印 `connected as @<handle>`。若显示未运行，
查看日志末尾。

```
tm im start --config alice
tm im status --config alice
tm im logs -f --config alice     # Ctrl+C 只停跟随，不停 bot
tm im me --config alice          # 从服务器打印 bot 身份
```

**6. 发一条测试消息。** 单聊要求双方是好友（否则返回 `40003`）；群聊无此限制。
应立刻收到 `ack_reply`，随后收到回答。调试时用 `tm im run --config alice`
前台运行，而不是 `start`。

最小配置文件示例（其余字段都有默认值）：

```toml
base_url = "https://tmim.example.com/v1"
api_key = "sk_live_..."
ws_url = "tmim.example.com/ws"
provider = "deepseek"
model = "deepseek-chat"
allow_tools = "read_only"
respond_in_groups = "mention"
```

常用调整，统一用 `tm im config set --config alice KEY=VALUE`：

| 目标 | 命令 |
|---|---|
| 群聊只在被 @ 时回复 | `respond_in_groups=mention` |
| 自动接受指定好友请求 | `auto_accept_friends=allowlist owner_handles=@a,@b` |
| 修改即时确认句 | `ack_reply="收到：{preview}"`（留空关闭） |
| 修改处理中提示 | `busy_reply="还在处理上一条，请稍等"`（留空关闭） |
| 直接编辑配置文件 | `tm im config edit --config alice` |

以下章节是每个字段与子命令的完整参考。

## 1. 前提

- 可访问的 tm_im 服务器。默认指向公共服务：REST 为
  `http://tmim.stkg.ltd/v1`，实时 WebSocket 为 `ws://tmim.stkg.ltd/ws`
  （由 REST host 的 `/ws` 推导）。自建部署时覆盖 `base_url` 以及 `ws_url`
  （或设置 `tcp_addr`）。
- 一个 tm_im 凭证。作为 bot，建议在 tm_im 客户端里创建一个**专用 Agent** 账号，
  复制其 `api_key`（`sk_live_...`）。不要复用你还会在别处登录的人类账号
  （见[故障排查](#7-故障排查)）。
- 单聊要求双方是**好友**（tm_im 对非好友单聊返回 `40003`）。群聊无此限制。

## 2. 配置文件

默认配置：

- Windows：`%APPDATA%\the-machine\im.toml`
- Linux/macOS：`~/.config/the-machine/im.toml`

命名配置（每个账号一个）位于 `<config>/configs/<name>.toml`，用
`tm im config new` 创建。见[第 4 节](#4-同机多账号)。

### 字段

| 字段 | 默认 | 含义 |
|---|---|---|
| `base_url` | `http://tmim.stkg.ltd/v1` | REST 基址 |
| `api_key` | – | Agent 凭证（`sk_live_...`） |
| `tcp_addr` | – | 实时 host:port（原生 TCP）；仅在显式设置时使用 |
| `ws_url` | `ws(s)://<base_url 的 host>/ws` | 实时 WebSocket 地址；优先于 `tcp_addr` |
| `device_id` | `tm` | 上报的设备标识 |
| `allow_tools` | `full` | `none` \| `read_only` \| `full`（见[安全](#6-安全)） |
| `respond_in_groups` | `always` | `mention` \| `always` \| `never` |
| `auto_accept_friends` | `none` | `none` \| `allowlist` \| `all` |
| `owner_handles` | `[]` | `allowlist` 模式下自动接受的好友 |
| `reply_max_chars` | `4000` | 回复分片大小（tm_im 文字上限 5000） |
| `sync_limit` | `200` | 实时 `SYNC` 分页大小 |
| `ack_reply` | `收到，正在处理：{preview}` | 收到消息后、Agent 处理前立即回复的确认句；`{preview}` 为收到的内容，留空则关闭 |
| `ack_preview_chars` | `60` | `{preview}` 最多保留的字符数 |
| `busy_reply` | `正在整理前一个消息任务，请稍等…` | 同一会话上一条还在处理时，新消息立即收到的提示；留空则关闭 |
| `provider` | – | 回复所用 provider 覆盖 |
| `model` | – | 回复所用模型覆盖 |

环境变量可覆盖文件：`TM_IM_BASE_URL`、`TM_IM_API_KEY`、`TM_IM_TCP_ADDR`、
`TM_IM_ALLOW_TOOLS`、`TM_IM_RESPOND_IN_GROUPS`、`TM_IM_ACK_REPLY`、
`TM_IM_BUSY_REPLY`。

### `tm im login`：默认配置与命名配置

`tm im login` 只写入一个配置文件。不带 `--config` 时更新**默认配置**
`<config>/im.toml`，`tm im run` / `tm im start` 省略 `--config` 时就用它。带
`--config <名字>` 时更新**命名配置** `<config>/configs/<名字>.toml`（不存在则
创建），运行时用 `--config <名字>` 选择。

默认配置：

```
tm im login                                # 交互输入 key
tm im login --api-key sk_live_...          # 非交互
tm im start                                # 使用 im.toml
```

命名配置：

```
tm im login --config alice                 # 交互输入 key，创建 configs/alice.toml
tm im login --config alice --api-key sk_live_...
tm im start --config alice                 # 使用 configs/alice.toml
```

`tm im config new <名字>` 是创建命名配置的另一种方式：它会提示输入 key，并可在
同一步设置模型、工具与群聊策略。`tm im login --config alice` 只设置凭证与端点，
但文件不存在时同样会创建。

| | 默认配置 | 命名配置 |
|---|---|---|
| 创建/更新 | `tm im login` | `tm im login --config alice` 或 `tm im config new alice` |
| 文件 | `<config>/im.toml` | `<config>/configs/alice.toml` |
| 运行时选择 | 省略 `--config` | `--config alice` |
| 适用场景 | 单个账号 | 多个账号并行 |

选项：`--api-key --base-url --tcp-addr --ws-url --config`。

其它字段（model、allow_tools、群聊策略等）用 `tm im config set` 或直接编辑文件。

## 3. 运行

### 前台

```
tm im run [--config <名字|路径>]
```

会打印解析到的模型与工具，连接后保持前台运行。`Ctrl+C` 退出。后台命令内部也是
启动这个 `run`。

选项（与 `start` 相同）：`--base-url --api-key --tcp-addr --ws-url
--allow-tools --respond-in-groups --provider/-p --model/-m --lang --config
--workers`。

### 即时确认

每条通过过滤的消息都会在 Agent 处理前先用 `ack_reply` 回一句，避免对方一直
等不到任何响应。`{preview}` 会替换为收到的内容（合并空白，按 `ack_preview_chars`
截断）。设为空字符串可关闭：

```
tm im config set ack_reply="收到：{preview}"
tm im config set ack_reply=      # 关闭
```

### 处理中再发消息

同一会话一次只处理一个任务。上一条还在处理时又收到新消息，bot 会立即用
`busy_reply`（默认 `正在整理前一个消息任务，请稍等…`）回一句，让对方知道消息已
排队；前一条处理完后会按 `seq` 顺序继续处理。设为空字符串可关闭：

```
tm im config set busy_reply="还在处理上一条，请稍等"
tm im config set busy_reply=      # 关闭
```

### 日志里的处理过程

每条收到的消息都会连同会话号与 `seq` 写入日志，随后输出 Agent 的处理过程：
回合、模型输出、思考内容、工具调用及参数、工具流式输出、工具结果（状态与耗时）、
token 用量、结束原因。过长文本会合并空白并按行截断。查看：

```
tm im logs -f
```

```
2026-01-01 12:00:00  im  <- [1001#2] 看一下磁盘占用
2026-01-01 12:00:00  im  [1001#2] turn 1 start
2026-01-01 12:00:00  im  [1001#2] model responding
2026-01-01 12:00:02  im  [1001#2] tool_calls=shell | stop=tool_use | tokens in=830 out=12
2026-01-01 12:00:02  im  [1001#2] tool shell start {"command": "du -sh ."}
2026-01-01 12:00:06  im  [1001#2] tool shell ok in 3.4s: 128M\t.
2026-01-01 12:00:08  im  [1001#2] turn 1 end
2026-01-01 12:00:08  im  [1001#2] assistant: 128 MB | stop=stop | tokens in=911 out=8
2026-01-01 12:00:08  im  [1001#2] agent finished stop=stop messages=4
2026-01-01 12:00:08  im  [1001#2] turn finished in 7.6s
2026-01-01 12:00:08  im  -> [1001#2] 128 MB
```

### 实时端点

长连接按以下规则选择：

- 设了 `ws_url` → **WebSocket**（如 `ws://host/ws`、`wss://host/ws`）。
- 否则设了 `tcp_addr` → **原生 TCP** `host:port`。
- 否则 → **WebSocket** `ws(s)://<base_url 的 host>/ws`，协议按 `base_url`
  推断（`http` → `ws`，`https` → `wss`）。

公共服务因此解析为 `ws://tmim.stkg.ltd/ws`，无需额外设置。其它部署需同时设置
REST 与实时端点：

```
tm im config set ws_url=other.example/ws   # 协议按 base_url 推断 ws/wss
tm im config set tcp_addr=host:8090        # 或用原生 TCP
tm im run --ws-url wss://other.example/ws  # 单次运行
```

不带协议时，`ws_url` 会按 `base_url` 继承协议（`http` → `ws`，`https` → `wss`）。

### 后台

```
tm im start [同上选项]          # 分离进程；日志写到 <实例>/im.log
tm im status [--config ...]    # 是否在跑；打印 pid 与日志路径
tm im logs [-n 40] [-f] [--config ...]
tm im stop   [--config ...]
```

`start` 会写 pid 文件，并按配置保证**单实例**。日志超过 5MB 会在下次启动时轮转为
`im.log.1`。

Windows 下子进程为分离进程（`DETACHED_PROCESS`）；Linux/macOS 下以新会话启动。
关闭终端不会停止它。

## 4. 同机多账号

每个配置文件拥有独立的实例目录，因此多个账号可并行运行，互不共享状态、会话、
pid、日志。

```
tm im config new alice --model deepseek-chat
tm im config new bob   --model deepseek-chat
tm im config list                          # 列出每个配置的全部字段及运行状态
tm im start --config alice
tm im start --config bob
```

### `tm im config`

| 命令 | 说明 |
|---|---|
| `tm im config model setup [--config <名字\|路径>]` | 交互选择回复 provider/model，并补存模型 key 与 tm_im key |
| `tm im config new <name>` | 创建 `<config>/configs/<name>.toml`。选项：`--base-url --api-key --tcp-addr --allow-tools --respond-in-groups --auto-accept-friends --provider/-p --model/-m --owner（可重复） --force` |
| `tm im config set [--config <名字\|路径>] KEY=VALUE...` | 修改已有配置的任意字段，例如 `model=deepseek-chat allow_tools=read_only owner_handles=@a,@b` |
| `tm im config edit [--config <名字\|路径>]` | 用 `$EDITOR` / `$VISUAL` 打开配置文件 |
| `tm im config list` | 列出每个配置的全部字段（API key 打码）及运行状态 |
| `tm im config path <name>` | 打印命名配置的路径 |
| `tm im config delete <name>` | 删除 `<name>.toml` 及其实例状态。运行中会拒绝；`--yes` 跳过确认，`--keep-state` 保留日志/会话/状态 |

`config new` 的默认与默认 `im.toml` 一致：`allow_tools=full`、
`respond_in_groups=always`、`auto_accept_friends=none`。

`config delete` 会同时删除配置文件和 `<instance>/`（日志、会话、`state.json`、
审计文件），因此重建同名配置时从全新状态开始。删除前先用
`tm im stop --config <name>` 停止实例。默认的 `im.toml` 不是命名配置，需直接删除
该文件及 `<config>/im/`。

### `--config` 解析规则

`--config` 接受**名字**或**路径**：

- `tm im start --config alice` → `<config>/configs/alice.toml`
- `tm im start --config ./hosts/prod.toml` → 该路径
- 省略 `--config` → 默认 `<config>/im.toml`

### 隔离目录结构

| 路径 | 内容 |
|---|---|
| `<config>/im/` | 默认实例 |
| `<config>/im/instances/<name>-<hash>/` | 命名实例 |
| `<instance>/im.pid` | 运行中的 pid |
| `<instance>/im.log` | 日志 |
| `<instance>/state.json` | 各会话游标与会话映射 |
| `<instance>/sessions/` | 每个会话一个 TM 会话 |
| `<instance>/audit.jsonl`、`approvals.json`、`changes.jsonl` | 审计、记住的授权、变更日志 |

## 5. 模型选择

回复所用模型按以下顺序解析（命中即止）：

1. `tm im run/start --provider/-p --model/-m`
2. 该实例配置文件里的 `provider` / `model`
3. `<config>/settings.toml` 里的 `provider` / `model`
4. 内置默认（`deepseek` / `deepseek-v4-pro`）

持久化可用 `tm im config set --config <name> provider=<id> model=<id>`，或直接编辑
文件。同一实例的所有会话共用该模型。

用 `tm --list-models` 查看已配置 key 的 provider（`*` 表示已设 key）；用
`tm --login <provider>` 存 key。

## 6. 安全

tm_im 输入是**不可信的**：任何能给 bot 发消息的人都能让它做任何事。默认
`allow_tools=full` 意味着这些请求可以在本机执行 shell，`tm im run`/`start`
开启时会打印警告。收紧方式：

- `allow_tools=read_only` — 仅 `read`、`grep`、`find`、`ls`。
- `allow_tools=none` — 纯对话，不带工具。

无人值守实例无法回答权限弹窗：`full` 自动放行，其余默认拒绝（除非权限策略已允许）。
回复以纯文本发送，按 `reply_max_chars` 分片。

## 7. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `kicked (reason=1) signed in from another device` 后停止 | 同一 tm_im 账号在别处也登录了（如网页端）。tm_im 每账号只允许一条连接。给 bot 用**专用 Agent** 账号，或关闭另一处会话。 |
| 别人发的消息没回复 | 单聊要求双方是好友，否则服务器返回 `40003`，bot 收不到。先加好友，或改用群聊。群聊还要检查 `respond_in_groups`。若连接正常，`ack_reply` 会立即回一句；连确认句都没有，说明消息根本没到 bot。 |
| `No such option: --base-url`（或 `im` 被当成 prompt） | 旧的 `uv tool` 启动器。刷新：`uv tool install --force "the-machine[tui]"`（先退出所有 `tm`）。 |
| `start` 失败 / uv tool 目录 "access denied" | 有 `tm` 正在运行占用了工具目录。退出后重装。 |
| 好友请求被忽略 | 默认 `auto_accept_friends=none`。设为 `allowlist` 并配 `owner_handles`，或设为 `all`。 |
| `start` 后 `status` 显示 "not running" | bot 提前退出（key 无效、服务器不可达、或被 KICK）。看 `tm im logs`。 |

## 8. 工作原理

- **写操作走 REST**（`POST /conversations/{id}/messages`），每条回复带
  `client_msg_id` 保证幂等。
- **收消息走实时长连接**（默认 WebSocket，设置 `tcp_addr` 时走原生 TCP）：
  鉴权（`AUTH`）、应用层心跳、`PUSH` 推送新消息；
  每次重连（以及检测到 `seq` 空洞时）用 `SYNC` 按序补齐。
- **排序与去重用 `seq`**：`seq <= last` 忽略，每会话最后处理的 `seq` 持久化在
  `state.json`。
- **每个会话一个 TM 会话**，保持多轮记忆；并发会话由小型 worker 池处理
  （按会话加锁）。同一会话正在处理时收到的新消息会先回 `busy_reply`，
  再按 `seq` 顺序处理。
- 无新增依赖：REST 客户端、protobuf 编解码、TCP 客户端均只用 Python 标准库。

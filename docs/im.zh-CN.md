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

## 1. 前提

- 可访问的 tm_im 服务器。默认 REST 为 `http://<host>:8080/v1`，实时长连接为
  `<host>:8090`（原生 TCP）。
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
| `base_url` | `http://localhost:8080/v1` | REST 基址 |
| `api_key` | – | Agent 凭证（`sk_live_...`） |
| `tcp_addr` | 由 `base_url` 推导的 `host:8090` | 实时 host:port（原生 TCP） |
| `ws_url` | – | 实时 WebSocket 地址；优先于 `tcp_addr` |
| `device_id` | `tm` | 上报的设备标识 |
| `allow_tools` | `full` | `none` \| `read_only` \| `full`（见[安全](#6-安全)） |
| `respond_in_groups` | `always` | `mention` \| `always` \| `never` |
| `auto_accept_friends` | `none` | `none` \| `allowlist` \| `all` |
| `owner_handles` | `[]` | `allowlist` 模式下自动接受的好友 |
| `reply_max_chars` | `4000` | 回复分片大小（tm_im 文字上限 5000） |
| `sync_limit` | `200` | 实时 `SYNC` 分页大小 |
| `provider` | – | 回复所用 provider 覆盖 |
| `model` | – | 回复所用模型覆盖 |

环境变量可覆盖文件：`TM_IM_BASE_URL`、`TM_IM_API_KEY`、`TM_IM_TCP_ADDR`、
`TM_IM_ALLOW_TOOLS`、`TM_IM_RESPOND_IN_GROUPS`。

### `tm im login`

写入或更新配置文件中的凭证与端点：

```
tm im login                                  # 提示输入 key，写入 im.toml
tm im login --config alice --api-key sk_live_...
```

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

### 实时端点

长连接是**显式选择**的（不只是从 `base_url` 推断）：

- 设了 `ws_url` → **WebSocket**（如 `tmim.stkg.ltd/ws`、`wss://host/ws`）。
- 否则设了 `tcp_addr` → **原生 TCP** `host:port`。
- 否则 → `<base_url 的 host>:8090`（原生 TCP）。

```
tm im config set ws_url=tmim.stkg.ltd/ws     # 协议按 base_url 推断 ws/wss
tm im config set tcp_addr=host:8090          # 或用原生 TCP
tm im run --ws-url wss://tmim.stkg.ltd/ws    # 单次运行
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
tm im config list                          # 列出配置及是否在运行
tm im start --config alice
tm im start --config bob
```

### `tm im config`

| 命令 | 说明 |
|---|---|
| `tm im config new <name>` | 创建 `<config>/configs/<name>.toml`。选项：`--base-url --api-key --tcp-addr --allow-tools --respond-in-groups --auto-accept-friends --provider/-p --model/-m --owner（可重复） --force` |
| `tm im config set [--config <名字\|路径>] KEY=VALUE...` | 修改已有配置的任意字段，例如 `model=deepseek-chat allow_tools=read_only owner_handles=@a,@b` |
| `tm im config edit [--config <名字\|路径>]` | 用 `$EDITOR` / `$VISUAL` 打开配置文件 |
| `tm im config list` | 列出配置及其 base URL、模型、运行状态 |
| `tm im config path <name>` | 打印命名配置的路径 |

`config new` 的默认与默认 `im.toml` 一致：`allow_tools=full`、
`respond_in_groups=always`、`auto_accept_friends=none`。

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
| 别人发的消息没回复 | 单聊要求双方是好友，否则服务器返回 `40003`，bot 收不到。先加好友，或改用群聊。群聊还要检查 `respond_in_groups`。 |
| `No such option: --base-url`（或 `im` 被当成 prompt） | 旧的 `uv tool` 启动器。刷新：`uv tool install --force "the-machine[tui]"`（先退出所有 `tm`）。 |
| `start` 失败 / uv tool 目录 "access denied" | 有 `tm` 正在运行占用了工具目录。退出后重装。 |
| 好友请求被忽略 | 默认 `auto_accept_friends=none`。设为 `allowlist` 并配 `owner_handles`，或设为 `all`。 |
| `start` 后 `status` 显示 "not running" | bot 提前退出（key 无效、服务器不可达、或被 KICK）。看 `tm im logs`。 |

## 8. 工作原理

- **写操作走 REST**（`POST /conversations/{id}/messages`），每条回复带
  `client_msg_id` 保证幂等。
- **收消息走实时 TCP 长连接**：鉴权（`AUTH`）、应用层心跳、`PUSH` 推送新消息；
  每次重连（以及检测到 `seq` 空洞时）用 `SYNC` 按序补齐。
- **排序与去重用 `seq`**：`seq <= last` 忽略，每会话最后处理的 `seq` 持久化在
  `state.json`。
- **每个会话一个 TM 会话**，保持多轮记忆；并发会话由小型 worker 池处理
  （按会话加锁）。
- 无新增依赖：REST 客户端、protobuf 编解码、TCP 客户端均只用 Python 标准库。

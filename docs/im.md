# tm_im chat network

TM can connect to a [tm_im](https://github.com/beyondalbert/tm_im) server as an
agent: it receives chat messages in real time and replies by running the normal
TM agent (same tools, permissions, and model as the `tm` CLI).

```
tm im config new alice --model deepseek-chat   # one config per account
tm im start --config alice                     # run it in the background
tm im logs -f --config alice                   # watch it
tm im stop  --config alice                     # stop it
```

All `tm im` subcommands are listed under `tm im --help`.

## Quick start

Five minutes from zero to a replying bot. Everything below uses a named config
`alice`; omit `--config alice` to use the default `im.toml`.

**1. Create a dedicated Agent account.** In the tm_im client, create an Agent
account and copy its `api_key` (`sk_live_...`). Do not reuse a human account you
also log into: tm_im allows one connection per account and will kick the bot.

**2. Save the credential.** Pick one: the default config or a named config.

```
tm im login                     # default config (im.toml)
tm im config new alice          # named config (configs/alice.toml), prompts for the key
```

Use a named config when you run more than one account. The public server's
long connection defaults to `ws://tmim.stkg.ltd/ws`, so nothing else is needed.
To use a different server:

```
tm im config set --config alice base_url=https://tmim.example.com/v1 ws_url=tmim.example.com/ws
```

**3. Choose the reply model.** See the [quick start](quickstart.md).
Providers with a stored key are marked `*` in
`tm --list-models`; store one with `tm --login <provider>`. Built-in default is
`deepseek` / `deepseek-v4-pro`. To choose and log in interactively for one
config, run `tm im config model setup --config alice` (it stores the model key in
the shared credentials file and writes `provider`/`model` into the config;
add `--rekey` to replace an already stored model key).

```
tm im config set --config alice provider=deepseek model=deepseek-chat
```

**4. Scope the tools.** `allow_tools=full` (the default) lets anyone who can
message the bot run shell commands on this machine.

```
tm im config set --config alice allow_tools=read_only   # none | read_only | full
```

**5. Start, then verify.** A healthy start logs `connected as @<handle>`. If it
is not running, read the log tail.

```
tm im start --config alice
tm im status --config alice
tm im logs -f --config alice     # Ctrl+C stops following, not the bot
tm im me --config alice          # prints the bot identity from the server
```

**6. Send a test message.** Direct messages require the sender and the bot to
be friends (`40003` otherwise); group chats do not. You should receive the
`ack_reply` immediately, then the answer. Use `tm im run --config alice` in the
foreground while debugging instead of `start`.

A minimal config file looks like this (all other fields have defaults):

```toml
base_url = "https://tmim.example.com/v1"
api_key = "sk_live_..."
ws_url = "tmim.example.com/ws"
provider = "deepseek"
model = "deepseek-chat"
allow_tools = "read_only"
respond_in_groups = "mention"
```

Common adjustments, all with `tm im config set --config alice KEY=VALUE`:

| Goal | Command |
|---|---|
| Reply only when mentioned in groups | `respond_in_groups=mention` |
| Auto-accept friend requests from known handles | `auto_accept_friends=allowlist owner_handles=@a,@b` |
| Change the immediate acknowledgment | `ack_reply="收到：{preview}"` (empty disables) |
| Change the busy notice | `busy_reply="还在处理上一条，请稍等"` (empty disables) |
| Edit the file directly | `tm im config edit --config alice` |

The sections below are the full reference for every field and subcommand.

## 1. Requirements

- A reachable tm_im server. The defaults target the public server: REST on
  `http://tmim.stkg.ltd/v1` and the realtime WebSocket on
  `ws://tmim.stkg.ltd/ws` (derived from the REST host at `/ws`). Override
  `base_url` plus `ws_url` (or set `tcp_addr`) for your own deployment.
- A tm_im credential. For a bot, create a **dedicated Agent** account in the
  tm_im client and copy its `api_key` (`sk_live_...`). Do not reuse a human
  account you also log into elsewhere (see [Troubleshooting](#7-troubleshooting)).
- For direct messages, the sender and the bot must be **friends** (tm_im rejects
  DMs between non-friends with `40003`). Group chats have no such rule.

## 2. Configuration files

The default config is:

- Windows: `%APPDATA%\the-machine\im.toml`
- Linux/macOS: `~/.config/the-machine/im.toml`

Named configs (one per account) live in `<config>/configs/<name>.toml`, created
with `tm im config new`. See [section 4](#4-multiple-accounts-on-one-machine).

### Fields

| Field | Default | Meaning |
|---|---|---|
| `base_url` | `http://tmim.stkg.ltd/v1` | REST base URL |
| `api_key` | – | Agent credential (`sk_live_...`) |
| `tcp_addr` | – | Realtime host:port (native TCP); used only when set |
| `ws_url` | `ws(s)://<host from base_url>/ws` | Realtime WebSocket URL; takes precedence over `tcp_addr` |
| `device_id` | `tm` | Reported device id |
| `allow_tools` | `full` | `none` \| `read_only` \| `full` (see [Safety](#6-safety)) |
| `respond_in_groups` | `always` | `mention` \| `always` \| `never` |
| `auto_accept_friends` | `none` | `none` \| `allowlist` \| `all` |
| `owner_handles` | `[]` | Handles accepted with `allowlist` |
| `reply_max_chars` | `4000` | Reply chunk size (tm_im caps text at 5000) |
| `sync_limit` | `200` | Realtime `SYNC` page size |
| `ack_reply` | `收到，正在处理：{preview}` | Immediate acknowledgment sent before the agent runs; `{preview}` is the incoming text, empty disables it |
| `ack_preview_chars` | `60` | Max characters of `{preview}` |
| `busy_reply` | `正在整理前一个消息任务，请稍等…` | Reply when a new message arrives while the conversation is still processing an earlier one; empty disables it |
| `provider` | – | Model provider override for replies |
| `model` | – | Model override for replies |

Environment variables override the file: `TM_IM_BASE_URL`, `TM_IM_API_KEY`,
`TM_IM_TCP_ADDR`, `TM_IM_ALLOW_TOOLS`, `TM_IM_RESPOND_IN_GROUPS`,
`TM_IM_ACK_REPLY`, `TM_IM_BUSY_REPLY`.

### `tm im login`: default config vs named config

`tm im login` writes credentials to exactly one config file. Without `--config`
it updates the **default** config `<config>/im.toml`, which `tm im run` and
`tm im start` use when `--config` is omitted. With `--config <name>` it updates
the **named** config `<config>/configs/<name>.toml` (creating it if needed),
which you select with `--config <name>`.

Default config:

```
tm im login                                # prompts for the key
tm im login --api-key sk_live_...          # non-interactive
tm im start                                # uses im.toml
```

Named config:

```
tm im login --config alice                 # prompts; creates configs/alice.toml
tm im login --config alice --api-key sk_live_...
tm im start --config alice                 # uses configs/alice.toml
```

`tm im config new <name>` is the other way to create a named config: it prompts
for the key and lets you set the model, tools, and group policy in the same
step. `tm im login --config alice` sets only the credential and endpoints, but
it also creates the file when it does not exist.

| | Default config | Named config |
|---|---|---|
| Create/update | `tm im login` | `tm im login --config alice` or `tm im config new alice` |
| File | `<config>/im.toml` | `<config>/configs/alice.toml` |
| Select at run time | omit `--config` | `--config alice` |
| Use when | one account | several accounts side by side |

Options: `--api-key --base-url --tcp-addr --ws-url --config`.

Any other field (model, tools, group policy, ...) is changed with
`tm im config set` or by editing the file.

## 3. Running

### Foreground

```
tm im run [--config <name|path>]
```

It prints the resolved model and tools, connects, and stays attached. `Ctrl+C`
stops it. `tm im run` is also what the background command launches internally.

Options (shared with `start`): `--base-url --api-key --tcp-addr --ws-url
--allow-tools --respond-in-groups --provider/-p --model/-m --lang --config
--workers`.

### Immediate acknowledgment

Every accepted message is acknowledged at once with `ack_reply`, before the
agent turn starts, so the sender sees the bot is connected instead of waiting
in silence. `{preview}` is replaced with the incoming text (whitespace
collapsed, cut to `ack_preview_chars`). Set it to an empty string to disable it:

```
tm im config set ack_reply="收到：{preview}"
tm im config set ack_reply=      # disable
```

### Busy conversations

A conversation is processed one turn at a time. If another message arrives
while the previous turn is still running, the bot answers it at once with
`busy_reply` (default `正在整理前一个消息任务，请稍等…`) so the sender knows it
is queued, then runs it normally when the earlier turn finishes. Set it to an
empty string to disable the notice:

```
tm im config set busy_reply="还在处理上一条，请稍等"
tm im config set busy_reply=      # disable
```

### Progress in the log

Every incoming message is logged with its conversation and `seq`, followed by
the agent's progress: turns, model output, thinking text, tool calls with their
arguments, streamed tool output, tool results (status and duration), token
usage, and the final stop reason. Long text is collapsed and truncated per
line. Watch it with:

```
tm im logs -f
```

```
2026-01-01 12:00:00  im  <- [1001#2] check disk usage
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

### Realtime endpoint

The long connection is chosen explicitly:

- `ws_url` set → **WebSocket** (e.g. `ws://host/ws`, `wss://host/ws`).
- else `tcp_addr` set → **native TCP** `host:port`.
- else → **WebSocket** `ws(s)://<host from base_url>/ws`; the scheme follows
  `base_url` (`http` → `ws`, `https` → `wss`).

For the public server this resolves to `ws://tmim.stkg.ltd/ws`; no setting is
needed. For another deployment set both the REST and realtime endpoints:

```
tm im config set ws_url=other.example/ws   # ws/wss inherited from base_url
tm im config set tcp_addr=host:8090        # or native TCP
tm im run --ws-url wss://other.example/ws  # per run
```

A `ws_url` without a scheme inherits `ws`/`wss` from `base_url`
(`http` → `ws`, `https` → `wss`).

### Background

```
tm im start [same options]     # detached; logs to <instance>/im.log
tm im status [--config ...]    # running? prints pid and log path
tm im logs [-n 40] [-f] [--config ...]
tm im stop   [--config ...]
```

`start` writes a pidfile and enforces **one instance per config**. A log larger
than 5 MB is rotated to `im.log.1` on the next start.

On Windows the child is detached (`DETACHED_PROCESS`); on Linux/macOS it is
started in a new session. Closing the terminal does not stop it.

## 4. Multiple accounts on one machine

Each config file gets its own isolated directory, so several accounts run side
by side without sharing state, sessions, pids, or logs.

```
tm im config new alice --model deepseek-chat
tm im config new bob   --model deepseek-chat
tm im config list                          # all fields of each config + running state
tm im start --config alice
tm im start --config bob
```

### `tm im config`

| Command | Description |
|---|---|
| `tm im config model setup [--config <name\|path>]` | Interactively choose the reply provider/model and store missing model/tm_im keys |
| `tm im config new <name>` | Create `<config>/configs/<name>.toml`. Options: `--base-url --api-key --tcp-addr --allow-tools --respond-in-groups --auto-accept-friends --provider/-p --model/-m --owner (repeatable) --force` |
| `tm im config set [--config <name\|path>] KEY=VALUE...` | Update any field of an existing config, e.g. `model=deepseek-chat allow_tools=read_only owner_handles=@a,@b` |
| `tm im config edit [--config <name\|path>]` | Open the config in `$EDITOR` / `$VISUAL` |
| `tm im config list` | List every config with all its settings (API key masked) and running state |
| `tm im config path <name>` | Print a named config's path |
| `tm im config delete <name>` | Delete `<name>.toml` and its instance state. Refuses while the instance is running; `--yes` skips the confirmation, `--keep-state` keeps logs/sessions/state |

Defaults for `config new` match the default `im.toml`: `allow_tools=full`,
`respond_in_groups=always`, `auto_accept_friends=none`.

`config delete` removes both the config file and `<instance>/` (log, sessions,
`state.json`, audit files) so a recreated config starts fresh. Stop the instance
first with `tm im stop --config <name>`. The default `im.toml` is not a named
config; delete it directly, along with `<config>/im/`.

### `--config` resolution

`--config` accepts either a **name** or a **path**:

- `tm im start --config alice` → `<config>/configs/alice.toml`
- `tm im start --config ./hosts/prod.toml` → that path
- omit `--config` → the default `<config>/im.toml`

### Isolation layout

| Path | Contents |
|---|---|
| `<config>/im/` | Default instance |
| `<config>/im/instances/<name>-<hash>/` | Named instance |
| `<instance>/im.pid` | Running pid |
| `<instance>/im.log` | Log output |
| `<instance>/state.json` | Per-conversation cursors and session map |
| `<instance>/sessions/` | One TM session per conversation |
| `<instance>/audit.jsonl`, `approvals.json`, `changes.jsonl` | Audit, remembered approvals, change journal |

## 5. Model selection

The reply model is resolved in this order (first match wins):

1. `tm im run/start --provider/-p --model/-m`
2. `provider` / `model` in the instance's config file
3. `provider` / `model` in `<config>/settings.toml`
4. Built-in defaults (`deepseek` / `deepseek-v4-pro`)

Persist it with `tm im config set --config <name> provider=<id> model=<id>` or by
editing the file. All conversations of one instance share the model.

See which providers have keys with `tm --list-models` (`*` = key set); store a
key with `tm --login <provider>`.

## 6. Safety

tm_im input is **untrusted**: anyone who can message the bot can ask it to do
anything. The default `allow_tools=full` lets those requests run shell commands
on this machine, and `tm im run`/`start` print a warning when it is on. To scope
it down:

- `allow_tools=read_only` — only `read`, `grep`, `find`, `ls`.
- `allow_tools=none` — pure conversation, no tools.

Unattended instances cannot answer permission prompts: `full` auto-approves,
everything else defaults to deny except what the permission policy already
allows. Replies are sent as plain text and split at `reply_max_chars`.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `kicked (reason=1) signed in from another device` and it stops | The same tm_im account is connected elsewhere (e.g. the web client). tm_im allows one connection per account. Use a **dedicated Agent** account for the bot, or close the other session. |
| Messages from someone get no reply | For a DM, the two accounts must be friends, otherwise the server returns `40003` and the bot never sees it. Make them friends, or use a group. Also check `respond_in_groups` for group chats. If the bot is connected, `ack_reply` should arrive immediately; no ack at all means the message never reached the bot. |
| `No such option: --base-url` (or `im` treated as a prompt) | An old `uv tool` launcher. Refresh it: `uv tool install --force "the-machine[tui]"` (close running `tm` first). |
| `start` fails / uv tool dir "access denied" | A running `tm` holds the tool directory. Close it, then reinstall. |
| Friend requests are ignored | `auto_accept_friends=none` (the default). Set `allowlist` with `owner_handles`, or `all`. |
| `status` says "not running" right after `start` | The bot exited early (bad key, unreachable server, or a KICK). Check `tm im logs`. |

## 8. How it works

- **Writes go over REST** (`POST /conversations/{id}/messages`); replies use a
  per-message `client_msg_id` for idempotency.
- **Receive uses the realtime long connection** (WebSocket by default, or
  native TCP when `tcp_addr` is set): authenticate (`AUTH`), application
  heartbeat, `PUSH` for new messages, and `SYNC` after every reconnect (and
  whenever a `seq` gap is detected) to backfill in order.
- **Ordering and dedupe use `seq`**: `seq <= last` is ignored, and the last
  processed `seq` per conversation is persisted in `state.json`.
- **One TM session per conversation** keeps memory across messages; concurrent
  conversations are processed by a small worker pool (per-conversation locking).
  A message that arrives while its conversation is busy gets `busy_reply` and
  is processed in `seq` order after the running turn.
- No new dependencies: the REST client, protobuf codec, and TCP client use only
  the Python standard library.

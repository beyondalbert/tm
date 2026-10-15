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

## 1. Requirements

- A reachable tm_im server. Defaults assume REST on `http://<host>:8080/v1` and
  the realtime long connection on `<host>:8090` (native TCP).
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
| `base_url` | `http://localhost:8080/v1` | REST base URL |
| `api_key` | – | Agent credential (`sk_live_...`) |
| `tcp_addr` | `<host from base_url>:8090` | Realtime host:port (native TCP) |
| `ws_url` | – | Realtime WebSocket URL; takes precedence over `tcp_addr` |
| `device_id` | `tm` | Reported device id |
| `allow_tools` | `full` | `none` \| `read_only` \| `full` (see [Safety](#6-safety)) |
| `respond_in_groups` | `always` | `mention` \| `always` \| `never` |
| `auto_accept_friends` | `none` | `none` \| `allowlist` \| `all` |
| `owner_handles` | `[]` | Handles accepted with `allowlist` |
| `reply_max_chars` | `4000` | Reply chunk size (tm_im caps text at 5000) |
| `sync_limit` | `200` | Realtime `SYNC` page size |
| `provider` | – | Model provider override for replies |
| `model` | – | Model override for replies |

Environment variables override the file: `TM_IM_BASE_URL`, `TM_IM_API_KEY`,
`TM_IM_TCP_ADDR`, `TM_IM_ALLOW_TOOLS`, `TM_IM_RESPOND_IN_GROUPS`.

### `tm im login`

Store or update the credential and endpoint in a config file:

```
tm im login                                  # prompts for the key, writes im.toml
tm im login --config alice --api-key sk_live_...
```

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

### Realtime endpoint

The long connection is chosen explicitly (it is not just inferred from
`base_url`):

- `ws_url` set → **WebSocket** (e.g. `tmim.stkg.ltd/ws`, `wss://host/ws`).
- else `tcp_addr` set → **native TCP** `host:port`.
- else → `<host from base_url>:8090` (native TCP).

```
tm im config set ws_url=tmim.stkg.ltd/ws     # ws/wss inherited from base_url
tm im config set tcp_addr=host:8090          # or native TCP
tm im run --ws-url wss://tmim.stkg.ltd/ws    # per run
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
tm im config list                          # shows each config and if it is running
tm im start --config alice
tm im start --config bob
```

### `tm im config`

| Command | Description |
|---|---|
| `tm im config new <name>` | Create `<config>/configs/<name>.toml`. Options: `--base-url --api-key --tcp-addr --allow-tools --respond-in-groups --auto-accept-friends --provider/-p --model/-m --owner (repeatable) --force` |
| `tm im config set [--config <name\|path>] KEY=VALUE...` | Update any field of an existing config, e.g. `model=deepseek-chat allow_tools=read_only owner_handles=@a,@b` |
| `tm im config edit [--config <name\|path>]` | Open the config in `$EDITOR` / `$VISUAL` |
| `tm im config list` | List configs with base URL, model, and running state |
| `tm im config path <name>` | Print a named config's path |

Defaults for `config new` match the default `im.toml`: `allow_tools=full`,
`respond_in_groups=always`, `auto_accept_friends=none`.

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
| Messages from someone get no reply | For a DM, the two accounts must be friends, otherwise the server returns `40003` and the bot never sees it. Make them friends, or use a group. Also check `respond_in_groups` for group chats. |
| `No such option: --base-url` (or `im` treated as a prompt) | An old `uv tool` launcher. Refresh it: `uv tool install --force "the-machine[tui]"` (close running `tm` first). |
| `start` fails / uv tool dir "access denied" | A running `tm` holds the tool directory. Close it, then reinstall. |
| Friend requests are ignored | `auto_accept_friends=none` (the default). Set `allowlist` with `owner_handles`, or `all`. |
| `status` says "not running" right after `start` | The bot exited early (bad key, unreachable server, or a KICK). Check `tm im logs`. |

## 8. How it works

- **Writes go over REST** (`POST /conversations/{id}/messages`); replies use a
  per-message `client_msg_id` for idempotency.
- **Receive uses the realtime TCP long connection**: authenticate (`AUTH`),
  application heartbeat, `PUSH` for new messages, and `SYNC` after every
  reconnect (and whenever a `seq` gap is detected) to backfill in order.
- **Ordering and dedupe use `seq`**: `seq <= last` is ignored, and the last
  processed `seq` per conversation is persisted in `state.json`.
- **One TM session per conversation** keeps memory across messages; concurrent
  conversations are processed by a small worker pool (per-conversation locking).
- No new dependencies: the REST client, protobuf codec, and TCP client use only
  the Python standard library.

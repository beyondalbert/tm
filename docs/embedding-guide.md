# Embedding TM: integration guide

This guide is the practical counterpart to
[`docs/design/embedding.md`](design/embedding.md) (protocol reference, security
model, roadmap). It answers three questions:

1. how do I wire a host to TM,
2. where do the model and API key come from,
3. what does a correct host implementation look like.

Everything below runs against a released `tm` install. No Python linking is
needed unless you want the SDK.

Prerequisites:

```bash
uv tool install "the-machine[all]"   # or: pip install "the-machine[all]"
tm --version                         # pin this version in your product image
tm serve --help                      # confirms the embedding flags exist
```

Python 3.8+ is supported (3.9+ for the Google provider). The `tm` executable is
the only entry point: `serve`, `mcp-serve`, and `acp` are subcommands of it.

---

## 1. Choose the integration shape

| Your host is… | Use | Section |
| --- | --- | --- |
| any language, needs streaming and approvals | `tm serve --stdio` | §2, §3 |
| Python, wants an awaitable API | `tm.sdk` | §4 |
| an MCP client (Claude Desktop, MCP editors) | `tm mcp-serve` | §5 |
| an ACP client (Zed-style editor) | `tm acp` | §6 |

All four share one implementation: the same policy, tool set, session store, and
audit log.

---

## 2. Minimal raw JSON-RPC session (copy-paste)

```bash
tm serve --stdio --mode policy --max-sessions 4
```

The process speaks JSON-RPC 2.0, one JSON object per line, on stdin/stdout.
stdout carries the protocol only; diagnostics go to stderr. A client loop:

```python
import json, subprocess

proc = subprocess.Popen(
    ["tm", "serve", "--stdio", "--mode", "policy"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
)
next_id = 0

def call(method, params=None):
    global next_id
    next_id += 1
    proc.stdin.write(json.dumps(
        {"jsonrpc": "2.0", "id": next_id, "method": method, "params": params or {}}
    ) + "\n")
    proc.stdin.flush()
    while True:                      # skip notifications
        message = json.loads(proc.stdout.readline())
        if message.get("id") == next_id:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message["result"]

info = call("initialize", {"clientInfo": {"name": "my-host"}})
turn = call("prompt", {"content": "summarize src/"})
while True:                          # consume notifications until the turn ends
    message = json.loads(proc.stdout.readline())
    if message.get("method") == "turn/end" and message["params"]["turnId"] == turn["turnId"]:
        print(message["params"]["status"], message["params"]["text"])
        break
    if message.get("method") == "event":
        print("event:", message["params"]["event"]["type"])
call("shutdown")
proc.wait(timeout=5)
```

Rules that matter for a real host:

- Read stdout continuously in one task/thread and dispatch; never block the
  reader on a `prompt` call.
- `prompt` returns `{sessionId, turnId, status:"running"}` immediately. The
  answer arrives in the `turn/end` notification for that `turnId`.
- `cancel` is a separate request and works while the turn is running.
- EOF on stdin means the process exits. Send `shutdown` for a clean stop.

---

## 3. Sessions, streaming, approvals

### 3.1 Session fields

```json
{"method":"session/create","params":{
  "cwd":"./packages/api",       // defaults to the process cwd
  "access":"read-only",          // normal | read-only | dry-run
  "tools":["read","grep"],       // optional allowlist, names from the session's tool set
  "model":"deepseek/deepseek-v4-pro"   // optional; default is the config default
}}
```

`session/info` returns the resolved session: `sessionId`, `path`, `cwd`,
`access`, `persisted`, `messageCount`, `tools`, `model`, `running`, `warnings`.
`session/list` lists persisted sessions (`open` marks ones live in this
process). `session/undo` reverts file changes through the same journal `/undo`
uses. `session/fork` copies a branch into a new session.

### 3.2 Events

`event` notifications carry the raw `AgentEvent` (`agent_start`, `turn_start`,
`message_start/update/end`, `tool_execution_*`, `turn_end`, `agent_end`,
`agent_notice`). They are for display only; treat `turn/end` as the result.

### 3.4 Method summary

| Method | Purpose |
| --- | --- |
| `initialize` | capabilities, default session, warnings, method list |
| `prompt` / `cancel` / `steer` / `followUp` | run and steer a turn |
| `approve` | answer `permission/request` (`--mode host`) |
| `session/create` / `open` / `list` / `info` | session lifecycle |
| `session/fork` / `compact` / `undo` / `close` / `delete` | branch, compress, revert, remove |
| `session/set-model` | switch model without restarting |
| `providers/list` / `models/list` | model picker data (no secrets) |
| `credentials/set` / `credentials/clear` | only with `--allow-host-credentials` |
| `shutdown` | clean stop |

`turn/end` includes what a code-assistant host needs:

```json
{"status":"done","stopReason":"stop","text":"...",
 "usage":{"input":1200,"output":300,"total":1500},
 "changedFiles":[{"path":"...","tool":"edit","added":2,"removed":1,
                  "exists":true,"diff":"--- a/...\n+++ b/..."}]}
```

`status` is `done` / `failed` / `aborted`. `changedFiles` is one entry per path,
diffed against the state at turn start; `diff` is `null` for binary or oversized
files, and non-file changes (packages, services) appear with `kind` and
`summary`.

### 3.3 Approval loop (`--mode host`)

With `--mode host`, actions the policy leaves as `ask` block until the host
answers:

```json
<-- {"jsonrpc":"2.0","method":"permission/request","params":{
      "sessionId":"...","requestId":"p-1","deadlineMs":120000,
      "action":{"kind":"file_write","target":"src/a.py","tool":"edit"},
      "reason":"no matching allow rule","options":["allow","allow_scope","deny"]}}
--> {"jsonrpc":"2.0","id":9,"method":"approve",
     "params":{"sessionId":"...","requestId":"p-1","decision":"allow"}}
```

No answer before `deadlineMs` denies and emits `permission/expired`. The audit
log records the decision as `host decision`, so it is distinguishable from a
human at a keyboard. Use `--mode policy` when the host has no UI: `ask` is then
denied immediately and only policy-allowed actions run.

---

## 4. Python SDK

```python
from tm.sdk import TmClient

async with TmClient.connect(cwd="./repo") as client:      # spawns tm serve
    client.on_permission(lambda request: "allow")          # default: deny

    session = await client.create_session(access="read-only")
    turn = await session.prompt("list the failing tests")
    result = await turn.wait()
    print(result.status, result.text)
    for change in result.changed_files:
        print(change.path, change.added, change.removed)
```

- `client.request(method, params)` for anything not wrapped.
- `client.create_session(cwd=..., access=..., tools=..., model=...)`,
  `client.open_session(path)`, `client.sessions()`, `client.info()`.
- `session.prompt(...)`, `session.set_model("provider/model")`,
  `session.compact(...)`, `session.undo(count)`, `session.fork()`,
  `session.close()`.
- `TmClient.connect(command=..., env=..., extra_args=[...])` to point at a
  different install, pass credentials via the environment, or add flags.

The SDK is a transport wrapper. It does not decide permissions; it only answers
the questions TM asked.

---

## 5. MCP

```bash
tm mcp-serve --mode policy --cwd /path/to/repo
```

Client configuration (Claude Desktop / MCP-aware editors):

```json
{
  "mcpServers": {
    "tm": {
      "command": "tm",
      "args": ["mcp-serve", "--mode", "policy", "--cwd", "/path/to/repo"]
    }
  }
}
```

Tools:

- `tm_run {prompt, cwd?, idempotencyKey?}` → text answer plus a `Changed files:`
  list; `isError` is true when the turn did not end `done`.
- `tm_session_info {}`.

On Windows use an absolute path (or `tm.exe`) as `command`, because GUI clients
do not inherit a shell `PATH`.

Each distinct `cwd` gets its own session. With the default `--mode policy` an MCP
call cannot approve anything, which is the point: the host cannot widen access
through the tool call. Close stdin to stop the server (MCP has no `shutdown`
method).

---

## 6. ACP

```bash
tm acp            # default --mode host: the editor answers permission requests
```

Supported: `initialize`, `authenticate`, `session/new`, `session/load` (open
sessions only), `session/prompt`, `session/cancel`, `session/update`
(`agent_message_chunk`, `tool_call`, `tool_call_update`), and
`session/request_permission` requests answered with
`{"outcome":{"outcome":"selected","optionId":"allow"}}`.

`session/prompt` blocks until the turn finishes and returns `{stopReason}`
(`end_turn`, `cancelled`, `max_turn_requests`). Not supported: MCP servers
passed by the client, slash commands, resource links, terminal/fs callbacks;
those methods answer `-32601`.

Status: the ACP adapter implements the subset above and is covered by protocol
tests, but it has not yet been validated against a specific editor build. The
JSON-RPC server (`tm serve`) is the interface with the heaviest test coverage;
prefer it when you can choose.

---

## 7. Model, provider, and API-key setup

TM talks to providers through its own configuration; the embedding protocol can
choose a model but **cannot carry an API key**. This is deliberate (see §9).

### 7.1 Where credentials live

Three supported patterns, in order of preference:

**A. Environment variables at spawn (recommended for embedded hosts).**

```python
await TmClient.connect(env={"DEEPSEEK_API_KEY": key})          # SDK
```
```python
subprocess.Popen(["tm", "serve"], env={**os.environ, "DEEPSEEK_API_KEY": key})
```

The key stays in the child process's environment; it never appears on the
protocol stream, in session files, or in logs.

**B. A host-owned config directory.**

Set `TM_CONFIG_DIR` to a directory the host controls and provision it up front:

```bash
TM_CONFIG_DIR=/var/lib/my-host/tm tm --login deepseek     # writes credentials.toml
```

This is the right pattern when several sessions or processes must share one
account, or when the host rotates keys.

**C. The user's own configuration (default).** `tm serve` uses the normal
`<config>/credentials.toml` and `models.toml`, so an IDE can simply reuse what
the user already set up with `tm --login`.

Custom endpoints (Ollama, vLLM, a gateway) are declared in `models.toml`; see the
"Custom providers and models" section of the README. `tm models sync <provider>`
refreshes the model list from the server.

### 7.2 Choosing a model over the protocol

```json
{"method":"providers/list"}
```
```json
{"providers":[{"id":"deepseek","name":"DeepSeek","api":"openai-completions",
               "defaultModel":"deepseek-v4-pro","modelCount":5,
               "hasCredentials":true}],
 "current":{"provider":"deepseek","id":"deepseek-v4-pro"}}
```

`hasCredentials` is a boolean, never a key. Localhost endpoints report `true`
because they need no key.

```json
{"method":"models/list","params":{"provider":"deepseek"}}
```
```json
{"models":[{"provider":"deepseek","id":"deepseek-v4-pro",
            "contextWindow":131072,"maxTokens":8192,"reasoning":true}]}
```

Then pick a model when creating a session, or switch a live one:

```json
{"method":"session/create","params":{"cwd":"./repo","model":"deepseek/deepseek-v4-pro"}}
{"method":"session/set-model","params":{"model":"deepseek/deepseek-v4-flash"}}
```

`session/set-model` is refused while a turn is running (`-32001`); cancel first.

### 7.3 When no usable model is configured

`tm serve` does **not** exit when the configured default cannot be resolved. It
starts with `session: null` and reports the reason:

```json
{"result":{"session":null,
           "warnings":["Unknown provider: nope"],
           "capabilities":{"modelSelection":true, ...}}}
```

`session/create` without a model then fails with `-32008` and the provider list,
so a host can show "connect a provider" instead of a dead process:

```json
{"error":{"code":-32008,
          "message":"no model is configured; pass \"model\" to session/create or run `tm --login <provider>`",
          "data":{"providers":["anthropic","deepseek","ollama", ...]}}}
```

The same code is returned for an unusable model reference:

```json
{"method":"session/set-model","params":{"model":"nope/none"}}
{"error":{"code":-32008,"message":"Unknown provider: nope"}}
```

A provider that exists but has no key is not an error: the session is created
with a warning, so the host can prompt for a key while still allowing local
models.

**D. Runtime injection (opt-in), when the host learns the key after launch.**

For an IDE that asks the user for a key in its own settings dialog, a backend
that reads keys from a vault per tenant, or key rotation without restarting:

```bash
tm serve --stdio --allow-host-credentials
```

```json
{"method":"credentials/set","params":{
  "provider":"deepseek",
  "apiKey":"sk-...",            // or "keyFile":"/run/secrets/deepseek"
  "persist":false                // default: memory only
}}
```
```json
{"result":{"provider":"deepseek","configured":true,"persisted":false,
           "masked":"sk-abc...7890","sessions":["<rebound session ids>"]}}
```

- Off by default: without the flag the method does not exist (`-32601`), and
  `initialize.capabilities.hostCredentials` is `false`. Feature-detect it.
- Memory-only unless `"persist": true`, which writes `credentials.toml` with
  `chmod 600`. A restart without persistence needs the key again.
- Sessions already open are rebound immediately to a provider built with the
  new key, so no restart and no model switch is required.
- The key is never echoed: the reply carries only a masked preview, and the key
  never enters events, session files, telemetry, or the audit log.
- Rotate by calling `credentials/set` again; `credentials/clear {provider}`
  forgets the injected key (add `"persist": true` to delete the stored one).
- Only enable this in a host that is the trust anchor for the key. The secret
  travels on the same bidirectional pipe the host reads events from, so do not
  enable it in a host that logs its raw wire traffic.

```python
client = await TmClient.connect(extra_args=["--allow-host-credentials"])
await client.request("credentials/set", {"provider": "deepseek", "apiKey": key})
```

### 7.5 Why injection is opt-in

A `credentials/set` method on an always-on channel would mean: the secret rides
the same stream the host logs and replays; a host bug can leak or rotate a key
for a process it does not own; and the audit trail cannot distinguish a key the
user stored from one a host asserted. The default therefore keeps secrets off
the protocol, and `--allow-host-credentials` makes the trade explicit for hosts
that genuinely need it.

## 8. White-label embedding: the end user never sees TM

This is the deployment where TM is a component inside another product. The
product's users have no TM config, no `tm --login`, no environment variables to
set, and no idea TM exists. The host owns everything:

| Product concern | TM mechanism |
| --- | --- |
| Where settings, sessions, audit live | `--config-dir <dir>` (one directory per tenant/instance) |
| Provider and model | host picks: `--provider/--model`, `providers/list`, `models/list`, `session/create {"model"}` |
| API key | env at spawn, or `--allow-host-credentials` + `credentials/set` (memory-only) |
| Assistant identity / output rules | `session/create {"instructions": "..."}` appends "Product instructions" to the system prompt |
| Tool access | `--read-only`, `--no-python`, `--no-tools`, `--dry-run`, `session/create {"tools": [...]}` |
| Approval UX | `--mode policy` (deny `ask`) or `--mode host` and render the product's own dialog |
| Conversation retention | `--no-session` (nothing on disk), or `session/delete {sessionId}` |
| Telemetry | off by default; never pass `--telemetry` |
| Error text | map codes to product messages (§9); never show TM's own wording |

Launch recipe:

```bash
tm serve --stdio   --config-dir /var/lib/acme/tm/tenant-42   --mode policy   --max-sessions 4   --model acme-gateway/gpt-4o   --no-extensions   --allow-host-credentials
```

```python
# The product backend, not the end user:
cmd = ["tm", "serve", "--stdio", "--config-dir", tenant_dir,
       "--mode", "policy", "--model", "acme-gateway/gpt-4o",
       "--allow-host-credentials"]
session = await client.create_session(
    cwd=workspace,
    access="normal",
    instructions="You are Acme's code assistant. Answer in the user's language.",
)
await client.request("credentials/set",
                     {"provider": "acme-gateway", "apiKey": tenant_key})
```

Notes that matter in this deployment:

- **Isolation.** `--config-dir` decides where `settings.toml`, `models.toml`,
  `credentials.toml`, `sessions/`, `changes/`, and `audit.jsonl` go. One
  directory per tenant or per instance keeps conversations and audit trails
  apart. The host may pre-write `settings.toml`/`models.toml` there, so the
  product never depends on a user-level TM install.
- **No user setup.** `tm serve` never prompts on stdin (stdin is the protocol),
  and project trust defaults to "no" for non-interactive runs, so a repository
  cannot load `.aiagent/extensions` or change the policy. Only `AGENTS.md`
  context is read; add `--no-context-files` if the product wants deterministic
  prompts.
- **Keys.** The host is the trust anchor: an environment variable per worker, or
  `credentials/set` with `--allow-host-credentials` (memory-only by default).
  Either way the end user never handles a key and nothing is written to the
  tenant directory unless `persist: true`.
- **Persona.** `instructions` is appended as "Product instructions" *after* TM's
  own rules and repository context. It changes tone and output format; it cannot
  widen permissions, because the policy is enforced in code before a tool runs.
- **Retention.** `--no-session` keeps nothing on disk (no resume, no
  `changedFiles` journal attribution). Otherwise delete a conversation with
  `session/delete`, which removes the session file after closing it.
- **Versioning.** Pin the `tm` version in the product image and read
  `initialize.protocolVersion`; TM is a dependency here, not a user-facing tool.
- **Support.** When the product's user reports a problem, the host's logs should
  carry the TM error code and `turnId`, not TM's sentence. Codes are stable;
  messages are not.

## 9. Errors, retries, and reconnects

| Code | Meaning | Host action |
| --- | --- | --- |
| -32700 / -32600 | malformed protocol message | fix the client; the server keeps running |
| -32601 | unknown method (also unsupported ACP method) | feature-detect via `initialize.methods` |
| -32602 | invalid params (bad path, unknown tool, unknown `requestId`) | fix the call |
| -32001 | a turn is already running | cancel or wait; retry with `idempotencyKey` |
| -32002 | no turn is running (`cancel`/`steer`) | ignore |
| -32003 | outbound approval request timed out | decide a policy fallback |
| -32004 | feature not enabled (e.g. approvals in `policy` mode) | change `--mode` |
| -32005 | sessions disabled (`--no-session`) | start without that flag |
| -32006 | session limit reached | close a session or raise `--max-sessions` |
| -32007 | unknown session id | re-create or `session/open` |
| -32008 | no usable model / credentials | run the setup flow in §7 |

Reconnect recipe:

1. `initialize` again, `session/open` the previous `path`.
2. Re-send the prompt with the same `idempotencyKey` if you are unsure whether
   the first attempt started. A replay returns the same `turnId` and, once the
   turn has finished, re-announces its `turn/end`.
3. Never re-send without a key: a second `prompt` on a busy session is `-32001`,
   but a second prompt after the turn finished starts new work.

---

## 10. Implementation checklist

- [ ] reader task that never blocks on a request
- [ ] `initialize` before anything else; store `session`, `warnings`, `methods`
- [ ] one session per workspace root (`session/create {cwd}`)
- [ ] `access: "read-only"` for analysis/review features
- [ ] answer `permission/request` (or run `--mode policy` and explain that `ask`
      actions are denied)
- [ ] render `turn/end.changedFiles` instead of running your own `git diff`
- [ ] handle `status: failed` and `aborted` as first-class outcomes
- [ ] `shutdown` + close stdin on exit; treat EOF as success
- [ ] credentials via env or `TM_CONFIG_DIR`; only use
      `--allow-host-credentials` if the host learns the key after launch
- [ ] surface `warnings` and `-32008` as "provider not configured" UI
- [ ] test with `--mode policy` in CI so no approval is ever granted silently

---

## 11. Where things live

| Concern | File |
| --- | --- |
| Protocol reference, security model | `docs/design/embedding.md` |
| Transport, dispatch, outbound requests | `src/tm/rpc/server.py` |
| Sessions, turns, undo, idempotency | `src/tm/rpc/host.py` |
| MCP / ACP adapters | `src/tm/rpc/{mcp,acp}.py` |
| CLI entry points | `src/tm/cli/{serve,mcp,acp}_cmd.py` |
| Python SDK | `src/tm/sdk/client.py` |
| Tests to copy from | `tests/test_sdk.py`, `tests/test_rpc_*.py` |

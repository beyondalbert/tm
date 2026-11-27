# TM Embedding Capability Design

Status: implemented (protocol 1.0). Scope: letting another tool drive TM as an
engine for code development over a stable process boundary.

Implemented: `tm serve --stdio` (JSON-RPC, multi-session, approvals, undo,
per-turn diffs), `tm mcp-serve` (MCP `tm_run` tool), `tm acp` (ACP subset), and
the Python SDK in `src/tm/sdk/`.

Not implemented, deliberately: host-provided tools, HTTP/multi-client transport,
per-session worktrees, and MCP servers passed by an ACP client. See §14.

## 1. Background and goal

Users ask for two related things:

- "another tool calls TM directly" (CI job, script, editor plugin), and
- "TM is embedded inside another tool" (IDE, desktop app, another agent).

They are the same problem at different lifetimes. TM already has a one-shot
escape hatch (`tm -p`, `tm --json`), but nothing that supports a *session*:
multi-turn conversations, streaming output, mid-turn approval prompts, and
cancellation.

The goal of this document is a protocol-first embedding interface, so a
TypeScript, Go, or Rust host can drive TM without linking Python, and so the
permission boundary stays inside the TM process.

## 2. Integration forms

| Form | Host | Needs | Interface |
| --- | --- | --- | --- |
| A. One-shot | CI, scripts | prompt in, result out | `tm -p` / `tm --json` |
| B. Long-lived session | editor, desktop app, agent framework | multi-turn, streaming, approval, cancel | `tm serve --stdio` |
| C. TM as someone's tool | MCP hosts, other agents | a single callable tool | `tm mcp-serve` |
| D. Editor-native agent | ACP clients (Zed-style) | sessions, streaming, permission requests | `tm acp` |

B is the trunk: A reuses B's event envelope, C and D are adapters over B.

## 3. Non-goals

- In-process Python SDK as the primary path (see §4).
- Remote/multi-client HTTP in this release. The protocol is transport-neutral so
  `--http` can be added later without changing the wire format.
- Host-provided tools (§14).
- Sandboxing. TM's isolation story remains "policy + OS permissions"; an
  embedder gets no stronger guarantee than the CLI.

## 4. Confirmed decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Boundary | Process, not library | The permission check must not be bypassable by the host |
| Transport | JSON-RPC 2.0, one JSON object per line, over stdio | No ports, no auth, host lifecycle governs TM |
| Framing | NDJSON | `Content-Length` framing buys nothing here and is harder in shells |
| stdout | Protocol only | Diagnostics go to stderr, otherwise hosts parse garbage |
| Approvals | Bridged to the host, never decided offline by it | Audit must say who approved |
| Policy input from host | Restrictions only | A host may narrow, never widen, the policy |
| Protocol version | Negotiated in `initialize` | Avoids silent breakage |
| Sessions | Several per process, one turn each | A host needs a task per file tree |
| Writes | One writer per session | The JSONL store is append-only with atomic commits |

Why not an in-process SDK: if a host constructs `Agent` itself and passes
`AutoAllowApprover`, then `src/tm/permissions/` is no longer a trust boundary.
Embedding must not create a path where the host can grant itself filesystem or
shell access. Over the protocol, `PermissionChecker` still runs in the TM
process; the host can only answer one pending question.

## 5. Architecture

```
host process (TS / Go / Python / Rust)
    |  JSON-RPC 2.0, NDJSON, stdio
    v
tm serve --stdio                    src/tm/cli/serve_cmd.py (shared build_host)
  |                                  tm mcp-serve -> rpc/mcp.py
  |                                  tm acp       -> rpc/acp.py
  v
RpcServer                           src/tm/rpc/server.py   transport, dispatch, outbound calls
ServerHost                          src/tm/rpc/host.py     sessions, turns, undo, diffs
  |
  +-- SessionRuntime                one Agent + Session + Approver + Journal
  +-- RpcApprover / AcpApprover     src/tm/rpc/{approver,acp}.py
  +-- collect_changes               src/tm/rpc/changes.py  per-turn diffs
  |
  +-- Agent / PermissionChecker / Policy / Session / Journal   unchanged
```

The server owns no agent state of its own; every method maps onto an existing
object. `AgentEvent` (already pydantic) is the wire format for streaming.

Building blocks in `tm.cli.serve_cmd.build_host` are shared by all three entry
points, so policy, tool selection, trust, and the session store never diverge.

## 6. Transport

- stdin: one JSON object per line. Blank lines are ignored.
- stdout: one JSON object per line, UTF-8, no pretty printing, `\n` terminated.
- stderr: free-form diagnostics; hosts must not parse it.
- EOF on stdin means the host is gone: cancel running turns, close sessions,
  exit 0.
- A malformed line answers a `-32700` error with `id: null` and does not kill
  the server.
- The server may also send requests to the host (currently only the ACP
  adapter, which uses it for `session/request_permission`).

`shutdown` is a request so the host gets an acknowledgement; the server then
stops reading and exits. A host that dies without `shutdown` relies on EOF.

stdin is read on a daemon thread and fed through a queue, so `shutdown` does not
hang waiting for a blocked `readline`, and interpreter shutdown is not blocked
either.

## 7. Protocol

### 7.1 Handshake

```json
--> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"acme-ide"}}}
<-- {"jsonrpc":"2.0","id":1,"result":{
      "protocolVersion":"1.0",
      "serverInfo":{"name":"tm","version":"0.10.45"},
      "capabilities":{"streaming":true,"approval":true,"sessions":true,
                      "hostTools":false,"structuredChanges":true,
                      "modelSelection":true,"hostCredentials":false},
      "mode":"host",
      "cwd":"D:/work/app",
      "maxSessions":4,
      "accessModes":["normal","read-only","dry-run"],
      "warnings":[],
      "session":{"sessionId":"...","path":"...jsonl","cwd":"...","access":"normal",
                 "persisted":true,"messageCount":0,"tools":["read","edit"],
                 "model":{"provider":"deepseek","id":"deepseek-v4-pro"},
                 "running":false,"warnings":[]},
      "methods":["approve","cancel","followUp","initialize","prompt",
                 "session/close","session/compact","session/create","session/fork",
                 "session/info","session/list","session/open","session/undo",
                 "shutdown","steer"]}}
```

`capabilities` is the negotiation surface. A host must check a capability before
calling the method that needs it; unknown methods answer `-32601` anyway.

`protocolVersion` follows `MAJOR.MINOR`: a new optional field bumps MINOR, a
removed or re-typed field bumps MAJOR. Hosts must refuse a MAJOR they do not
know.

### 7.2 Methods

Every session-scoped method takes an optional `sessionId` and defaults to the
session created at startup.

| Method | Params | Result |
| --- | --- | --- |
| `initialize` | `clientInfo?` | see above |
| `prompt` | `content`, `sessionId?`, `idempotencyKey?` | `{sessionId, turnId, status}` |
| `cancel` | `sessionId?`, `turnId?` | `{}` |
| `steer` | `content`, `sessionId?` | `{}` |
| `followUp` | `content`, `sessionId?` | `{}` |
| `approve` | `requestId`, `decision`, `sessionId?`, `remember?` | `{resolved: true}` |
| `session/info` | `sessionId?` | the session object from §7.1 |
| `session/create` | `cwd?`, `access?`, `tools?: string[]`, `model?`, `instructions?` | session object |
| `session/open` | `path` | session object |
| `session/set-model` | `model`, `sessionId?` | `{sessionId, model, warnings}` |
| `providers/list` | - | `{providers: [...], current}` |
| `models/list` | `provider?` | `{models: [...]}` |
| `credentials/set` | `provider`, `apiKey` or `keyFile`, `persist?` | `{configured, persisted, masked, sessions}` (opt-in) |
| `credentials/clear` | `provider`, `persist?` | `{configured, persisted}` (opt-in) |
| `session/list` | `cwd?` | `{sessions: [...]}` |
| `session/fork` | `sessionId?`, `entryId?` | session object |
| `session/compact` | `sessionId?`, `instructions?`, `keepRecent?` | `{compacted, messageCount}` |
| `session/undo` | `sessionId?`, `count?` | `{undone: string[]}` |
| `session/close` | `sessionId?` | `{closed: id}` |
| `session/delete` | `sessionId?` | `{deleted, path}` (removes the session file) |
| `shutdown` | - | `{}` then exit |

`prompt` returns before the turn finishes; progress arrives as notifications.
One turn per session may run at a time.

`idempotencyKey` makes a host retry safe. The mapping is recorded before the
turn starts and replaced with its outcome, so a retry during the turn gets the
same `turnId` (`"replayed": true`), and a retry after the turn finished
re-announces the stored `turn/end` notification before answering. Keys live in
the session store (`rpc.idempotency` values) when the session is persisted, so a
later process that reopens the session replays the same outcome.

`access` narrows a session:

- `normal`: the process tool set.
- `read-only`: only read-only tools, regardless of the process flags.
- `dry-run`: the same tools, but `Agent.dry_run` is on.

`tools` narrows further to a name allowlist. Unknown names are
`-32602` and the session is not created.

`session/create {instructions}` appends host-supplied text as "Product
instructions" to the system prompt, after TM's rules and repository context. It
cannot widen permissions: the policy is enforced before any tool runs.

`session/delete` closes the session and removes its file; it refuses while a turn
runs and with `--no-session` (`-32005`).

`session/open` accepts only files inside the sessions directory. A path
elsewhere is `-32602`; this keeps a host from loading an arbitrary file as a
session.

`session/undo` reuses the change journal: reversible `applied` entries for that
session, newest first, reverted in reverse order, then marked `reverted`.

### 7.3 Events

```json
<-- {"jsonrpc":"2.0","method":"event","params":{
      "sessionId":"...", "turnId":"...", "seq":12,
      "event":{"type":"tool_execution_start","tool_call_id":"c1",
               "tool_name":"edit","arguments":{"path":"src/a.py"}}}}
```

`event` is the serialized `tm.core.events.AgentEvent`, unchanged. `seq` is
monotonic per process and lets a host detect dropped lines.

The turn ends with a dedicated notification, which is what non-UI hosts read:

```json
<-- {"jsonrpc":"2.0","method":"turn/end","params":{
      "sessionId":"...", "turnId":"...", "status":"done",
      "stopReason":"stop", "text":"...", "messageCount":8,
      "usage":{"input":1200,"output":300,"cache_read":0,"cache_write":0,"total":1500},
      "error":null,
      "changedFiles":[
        {"path":"D:/work/app/src/a.py","kind":"file","tool":"edit",
         "summary":"edited a.py","status":"applied","exists":true,
         "added":2,"removed":1,"truncated":false,
         "diff":"--- a/a.py\n+++ b/a.py\n@@ ..."}]}}
```

`status` is `done`, `failed`, or `aborted`. `stopReason` is the agent's own
reason (`stop`, `max_turns`, `error`, `aborted`). `usage` is the session total.

`changedFiles` comes from the change journal, one entry per path, newest state
but diffed against the state at turn start (the journal stores the content
before each change). Diffs are capped (2000 lines, 512 KiB read per file) and
`truncated` says so. `added`/`removed`/`diff` are `null` when the previous or
current content is binary or too large. Non-file changes (packages, services)
appear with their `kind` and `summary` and no diff. A session without
persistence (`--no-session`) has no journal id, so a single-session process
attributes every change since the turn started to that turn.

### 7.5 Models and credentials

TM owns provider access: it loads `credentials.toml`, `models.toml`, and the
environment when it starts, and every session uses those credentials. The
protocol can *choose* a model but never carries a secret.

- `providers/list` reports `id`, `name`, `api`, `defaultModel`, `modelCount`, and
  a boolean `hasCredentials` (never a key). Localhost endpoints report `true`.
- `models/list {provider?}` reports the catalog the host can offer in a picker.
- `session/create {model}` and `session/set-model {model}` accept
  `provider/model` or a model id. `set-model` is refused while a turn runs.
- A provider without a key is not an error: the session is created with a
  `warnings` entry so the host can ask for a key.
- Startup is lenient: an unresolvable default model produces
  `session: null`, a startup warning, and `-32008` on `session/create` without a
  model. A host can therefore start TM, query `providers/list`, and let the user
  configure a provider instead of seeing a dead process.

Default patterns (no secret on the wire): an environment variable set when
spawning TM (`TmClient.connect(env=...)`), a host-owned `TM_CONFIG_DIR`
provisioned with `tm --login`, or the user's existing configuration.

`credentials/set` and `credentials/clear` exist for the case where the host
learns the key *after* launch, but only with `tm serve --allow-host-credentials`:

- absent from `methods` and `capabilities.hostCredentials: false` by default;
- memory-only unless `persist: true` (which writes `credentials.toml`, mode 600);
- the reply carries a masked preview, never the key; the key never reaches
  events, session files, telemetry, or the audit log;
- existing sessions are rebound to a provider rebuilt with the new key, so
  rotation needs no restart;
- injecting is a consent decision: the secret crosses the same channel the host
  logs and replays, so a host that records its raw traffic must not enable it.

See [`docs/embedding-guide.md`](embedding-guide.md#7-model-provider-and-api-key-setup).

### 7.4 Errors

| Code | Meaning |
| --- | --- |
| -32700 | parse error (malformed JSON / not an object) |
| -32600 | invalid request (bad `jsonrpc`, missing `method`) |
| -32601 | unknown method |
| -32602 | invalid params (also: unknown `requestId`, bad tool name, bad path) |
| -32603 | internal error (`data` carries `"Type: message"`) |
| -32001 | busy: a turn is already running |
| -32002 | no turn is running (`cancel`/`steer`/`followUp`) |
| -32003 | outbound request timed out (approval) |
| -32004 | unsupported capability (also: `credentials/set` without `--allow-host-credentials`) |
| -32005 | sessions are unavailable (`--no-session`) |
| -32006 | session limit reached (`data.maxSessions`) |
| -32007 | unknown session id (`data.sessionId`) |
| -32008 | no usable model or provider (see §7.5) |

## 8. Approval bridge

`Authorization` in TM is: policy decides allow/deny/ask, and only `ask` reaches
the approver (`src/tm/permissions/gate.py`). The bridge adds one implementation
of the existing `Approver` protocol, so nothing in the gate changes.

```
--> {"jsonrpc":"2.0","method":"permission/request","params":{
       "sessionId":"...", "requestId":"p-7f1",
       "action":{"kind":"shell","target":"pytest -q","tool":"shell"},
       "reason":"no matching allow rule",
       "options":["allow","allow_scope","deny"],
       "deadlineMs":120000}}
<-- {"jsonrpc":"2.0","id":42,"method":"approve",
     "params":{"sessionId":"...","requestId":"p-7f1","decision":"allow","remember":false}}
```

- `allow` approves this action once; `allow_scope` also persists the scope
  (`ApprovalMemory`), same as answering `a` in the TUI; `deny` refuses.
- No answer before `deadlineMs` denies and sends `permission/expired`.
- An unknown or already-answered `requestId` is `-32602`, not a silent success.
- `ApprovalOutcome.source` records who decided. `RpcApprover` reports
  `"host"`, so `audit.jsonl` says `host decision` instead of `user decision`.
  Without this, an automated pipeline's audit trail cannot be told apart from a
  human at a keyboard.

Three process modes, chosen at startup and reported in `initialize`:

| `--mode` | `ask` actions | Use |
| --- | --- | --- |
| `policy` (default) | denied, no notification | CI, batch, MCP |
| `host` | bridged to the host, timeout denies | IDE, desktop app |
| `yolo` | allowed, audited as `host decision`, policy `deny` still wins | trusted local scripting |

`host` is not the default on purpose: it lets a host approve actions, and a host
that always says yes is exactly the risk the policy exists to contain.

For ACP, the same decision arrives as an outbound request instead of a
notification inversion:

```
<-- {"jsonrpc":"2.0","id":"tm-3","method":"session/request_permission","params":{
      "sessionId":"...","toolCall":{"toolCallId":"perm-1","title":"shell: pytest -q",
      "kind":"execute","status":"pending","rawInput":{...}},
      "options":[{"optionId":"allow","name":"Allow once","kind":"allow_once"},
                 {"optionId":"allow_scope","name":"Always allow","kind":"allow_always"},
                 {"optionId":"deny","name":"Reject","kind":"reject_once"}]}}
--> {"jsonrpc":"2.0","id":"tm-3","result":{"outcome":{"outcome":"selected","optionId":"allow"}}}
```

## 9. Sessions, concurrency, limits

- A process hosts up to `--max-sessions` sessions (default 4), each with its own
  cwd, access mode, tool allowlist, `Agent`, session file, and approver.
- One turn per session. A second `prompt` on the same session is `-32001`
  (busy) rather than queueing, so a host cannot build an unbounded backlog by
  retrying. The same prompt with the same `idempotencyKey` is not an error: it
  returns the existing turn id.
- `cancel` aborts the in-flight model request and stops a running tool, reusing
  `Agent.abort()`. The turn then ends with `status: "aborted"`.
- `steer` and `followUp` reuse `Agent.steer()` / `Agent.follow_up()`: the
  message lands at the next safe point of the running turn. They require a
  running turn (`-32002` otherwise).
- Each session's cwd drives `AGENTS.md` discovery, the policy location
  (`<cwd>/.aiagent/policy.toml`), project trust, extension discovery, and every
  tool's relative paths. There is no `chdir`: `_build_agent(cwd=...)` carries the
  directory explicitly.
- Project trust is resolved once per cwd without prompting: `--approve`,
  `--no-approve`, a saved decision, then `default_project_trust`, with `ask`
  treated as "no" because stdin is the protocol channel.
- Closing a session aborts its turn, unsubscribes its listener, and closes its
  storage. Persisted sessions remain on disk and can be reopened.

## 10. Security model

Threats considered, and the answer:

| Threat | Mitigation |
| --- | --- |
| Host widens permissions | No policy override in the protocol; only `read-only`/dry-run/tool narrowing |
| Host auto-approves everything | `--mode policy` default; `yolo` must be explicit and is audited |
| Host reads another process's data | stdio is per-process; no listening socket |
| Host corrupts the session store | Atomic JSONL commits; one writer per session |
| Host loads an arbitrary file as a session | `session/open` restricted to the sessions directory |
| Host injects terminal escapes into logs | Events are JSON; the host renders, TM never writes ANSI to stdout |
| Project-local extensions run because of a host run | Same `TrustManager` rules; `--approve` is opt-in and logged to stderr |

Two invariants for reviewers:

1. Nothing in `src/tm/rpc/` may construct a policy or bypass `PermissionChecker`.
2. Nothing in `src/tm/rpc/` may write to stdout except `RpcServer.send`.

## 11. Adapters

### 11.1 MCP (`tm mcp-serve`)

Exposes two tools over the MCP stdio transport:

- `tm_run {prompt, cwd?, idempotencyKey?}`: runs a turn to completion and returns
  the final text plus a `Changed files:` list. `isError` is true when the turn
  did not end `done`.
- `tm_session_info {}`.

Each distinct `cwd` gets its own session. Default `--mode policy`: an MCP call
cannot approve anything, so only policy-allowed actions run. `shutdown` is not an
MCP method; hosts end the process by closing stdin.

### 11.2 ACP (`tm acp`)

Subset of the Agent Client Protocol: `initialize`, `authenticate`,
`session/new`, `session/load` (open sessions only), `session/prompt`,
`session/cancel` (notification), `session/update` notifications
(`agent_message_chunk`, `tool_call`, `tool_call_update`), and
`session/request_permission` requests. `session/prompt` blocks until the turn
ends and answers `{stopReason}` (`end_turn`, `cancelled`, `max_turn_requests`).

Not covered: MCP servers passed by the client, slash commands, resource links,
and terminal/fs callbacks. An editor that needs those gets `-32601` for the
method it calls; nothing is silently ignored.

### 11.3 Python SDK (`tm.sdk`)

```python
from tm.sdk import TmClient

async with TmClient.connect(cwd="D:/work/app") as client:
    client.on_permission(lambda request: "allow")   # or "deny" / "allow_scope"
    session = await client.create_session(access="read-only",
                                         model="deepseek/deepseek-v4-pro")
    turn = await session.prompt("explain src/tm/rpc")
    result = await turn.wait()
    print(result.status, result.text)
    for change in result.changed_files:
        print(change.path, change.added, change.removed)
```

`TmClient.connect` spawns the CLI (`sys.executable -m tm.cli.main serve --stdio`)
with `stderr` inherited, and accepts `env=` for provider keys. `DEFAULT_COMMAND` and `extra_args` let a host point at a
different installation or add flags such as `--model`. The client is a thin
transport wrapper; it never decides permissions on its own. The default
permission answer is `deny`.

## 12. Mapping onto existing code

| Piece | File |
| --- | --- |
| Protocol constants, envelopes, validation | `src/tm/rpc/protocol.py` |
| Transport, dispatch, outbound requests | `src/tm/rpc/server.py` |
| Sessions, turns, undo, idempotency | `src/tm/rpc/host.py` |
| Approval bridge (notification style) | `src/tm/rpc/approver.py` |
| Per-turn changed files and diffs | `src/tm/rpc/changes.py` |
| MCP adapter | `src/tm/rpc/mcp.py`, `src/tm/cli/mcp_cmd.py` |
| ACP adapter and approver | `src/tm/rpc/acp.py`, `src/tm/cli/acp_cmd.py` |
| Shared host construction, model catalog, credential injection, `tm serve` | `src/tm/cli/serve_cmd.py` |
| Python SDK | `src/tm/sdk/client.py` |
| Explicit cwd plumbing | `src/tm/cli/main.py` (`_build_agent`, `_system_prompt`, `_load_resources`, `_resolve_trust`) |
| Audit attribution | `src/tm/permissions/{approval,gate}.py` |

## 13. Tests

| Area | File |
| --- | --- |
| Envelopes, error codes, param validation | `tests/test_rpc_protocol.py` |
| Transport, concurrency, notifications, outbound requests, shutdown | `tests/test_rpc_server.py` |
| Approval bridge round-trip, scope, timeout, duplicate answer | `tests/test_rpc_approver.py` |
| Turns, events, busy, cancel, idempotency, changed files | `tests/test_rpc_host.py` |
| Session lifecycle, limits, open restriction, undo, close | `tests/test_rpc_sessions.py` |
| Journal-to-diff conversion | `tests/test_rpc_changes.py` |
| MCP initialize/list/call/errors | `tests/test_rpc_mcp.py` |
| ACP subset, streaming updates, cancel, permission mapping | `tests/test_rpc_acp.py` |
| SDK against a real subprocess, model catalog, provider selection, credential injection | `tests/test_sdk.py` |

## 14. Deliberately not implemented

- **Host-provided tools** (`tool/register`, `tool/invoke`): powerful, but they
  need a trust label in the audit trail and a way to keep host code inside the
  permission story. Phase 4.
- **HTTP transport / one server many clients**: needs auth and a per-client
  session map. The wire format already permits it.
- **Per-session worktrees**: `session/create { worktree: true }` would need
  git worktree lifecycle and merge-back semantics.
- **MCP servers supplied by an ACP client**: `session/new` accepts the field and
  ignores it.
- **Compaction as a background job**: `session/compact` is synchronous; the agent
  loop still compacts inline before a request.

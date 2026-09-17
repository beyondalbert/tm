# TM Device Management Capability Design

Status: draft (v0.1). Scope: making TM able to manage and use the current
machine (and later, servers) through AI, not just read files and run shell.

## 1. Background and goal

TM's core goal is to let a user **manage and use the hardware and software of
the current machine through AI**. Today TM is a general file + shell agent: it
can read, write, edit, search, and run shell commands, gated by a permission
policy. That is not enough to operate a machine; the missing pieces are
perception, process/service lifecycle, authority, reversibility, autonomy, and
remote access.

This document defines the target architecture, the interfaces, the safety
model, and a phased delivery plan.

## 2. Scope

In scope:

- Local machine first; remote servers (SSH) in a later phase.
- Windows and Linux are equal-priority targets.
- Linux support targets mainstream systemd distributions (apt / dnf).
- Python as the general "solve it yourself" fallback.
- Reversible changes: mandatory snapshot before dangerous operations.
- Scheduling through OS-native facilities.
- Guided, manual privilege elevation.

Non-goals (v1):

- GUI automation (optional, fragile, later phase).
- Deep macOS support (best-effort only).
- Sandboxing/containers as a security boundary.
- Persisted elevation credentials for unattended use.

Confirmed decisions:

| Decision | Choice |
| --- | --- |
| Platform priority | Windows and Linux equal |
| Elevation | Guided manual (UAC / sudo / pkexec), no stored secrets |
| Autonomy | Scheduled / triggered runs required |
| Reversibility | Mandatory backup/rollback before dangerous changes |
| Linux scope | Mainstream systemd distros (apt/dnf) |
| Scheduling | OS-native (Task Scheduler / systemd timer / cron) |
| Snapshot coverage | All resource kinds |
| First deliverable | This design document, then Phase A |

## 3. Architecture

```
tools (python / monitor / system_info / process / package / service / schedule)
        |  depend only on the abstractions
        v
Host abstraction (src/tm/host/)   WindowsHost | LinuxHost
        |  identity/system/resources/software/processes/services/ports/
        |  schedule/elevated_argv/spawn/kill_tree/paths
        v
Safety + reversibility (src/tm/safety/)   SystemChange: prepare->apply->verify->rollback
        |
        v
OS / hardware
```

Two new infrastructure layers carry the design:

1. **Host abstraction** (`src/tm/host/`): all platform differences (admin
   detection, services, package managers, scheduling, elevation argv, paths)
   are collected here so tools never branch on the OS.
2. **Safety/reversibility** (`src/tm/safety/`): every dangerous mutation goes
   through a `SystemChange` that snapshots, applies, verifies, and can roll
   back, recorded in a change journal.

## 4. Host abstraction (`src/tm/host/`)

Target interface (implemented incrementally by phase):

```
class Host(ABC):
    name: str
    @classmethod detect() -> Host                 # by sys.platform
    identity() -> Identity                        # user, elevated, groups
    system() -> SystemInfo                        # os, distro, version, arch, hostname, kernel
    resources() -> Resources                      # cpu, memory, disks, gpus (best-effort)
    software() -> Software                        # which(), package_managers(), python_runtimes()
    processes(limit) -> list[ProcessInfo]
    listening_ports() -> list[PortInfo]
    services() -> list[ServiceInfo]
    service_action(name, action: start|stop|restart) -> None
    schedule(spec: ScheduleSpec) -> str
    unschedule(id) -> None
    elevated_argv(argv) -> list[str]              # Windows RunAs; POSIX sudo/pkexec
    spawn(argv, cwd, env, log_path, detached) -> int
    kill_tree(pid) -> None
    paths() -> HostPaths
```

Phase A implements `identity`, `system`, `resources`, `software`, and the
shared probing helpers. Later phases add the process/service/schedule/elevation
surface.

Implementation matrix:

| Capability | WindowsHost | LinuxHost |
| --- | --- | --- |
| Elevation check | admin token | `os.geteuid() == 0` |
| Services | SCM (`sc` / PowerShell) | `systemctl` |
| Package managers | winget / choco / scoop | apt / dnf |
| Scheduling | Task Scheduler (`schtasks`) | systemd timer / cron |
| Registry snapshot | `reg export` | n/a |
| Processes / ports | `Get-Process` / `Get-NetTCPConnection` | `ps` / `ss` |

Probing is best-effort: every external call is wrapped so a missing tool or a
permission error never fails the probe. Tests use a `FakeHost`.

## 5. Safety and reversibility (`src/tm/safety/`)

```
class SystemChange(ABC):
    kind: ChangeKind          # FILE/REGISTRY/SERVICE/PACKAGE/NETWORK/SCHEDULE/PROCESS
    description: str
    reversible: bool
    prepare(host, journal) -> Snapshot
    apply(host) -> ChangeResult
    verify(host) -> VerifyResult
    rollback(host, snapshot) -> None
```

Risk levels: `safe`, `caution`, `destructive`. A `destructive` change requires a
snapshot created by `prepare()` plus explicit approval; without a snapshot it is
refused. A change that cannot be rolled back is labelled as such.

Snapshot strategies by resource kind:

- FILE: copy to the backup store, or a shadow git repository for directories.
- REGISTRY: `reg export KEY backup.reg`.
- SERVICE: record start type, command, and the unit file.
- PACKAGE: record installed versions and `apt-mark showmanual`.
- NETWORK: dump current configuration (`netsh` / `ip addr`).
- SCHEDULE: record the task XML or unit file.

Change journal: `<config>/changes/changes.jsonl` with
`{id, ts, session, tool, kind, description, target, reversible, undo, status}`.
Rollback is exposed as `/undo [n]`. Implemented snapshots cover file content,
package install/uninstall, and service start/stop; process changes are recorded
as audit-only (non-reversible) entries.

## 6. Execution tools

- `system_info` (static, read-only, replay-safe): system, resources, software.
- `monitor` (dynamic, read-only): metrics, top processes, listening ports,
  service states, recent log lines.
- `python`: writes the code to a workspace script, runs it with a configurable
  interpreter (default `sys.executable`), streams output, enforces timeout and
  truncation, pre-checks and installs `packages`, and returns the traceback on
  failure. This is the general fallback for goals that shell cannot express.
- `process`: `start` (detached with logging), `status`, `logs`, `stop`,
  `restart`; job metadata persisted. Fixes the current gap where `shell` blocks
  and kills its process tree on timeout.
- `package`: install/uninstall/query, through `SystemChange(PACKAGE)`.
- `schedule`: creates Task Scheduler / systemd timer entries via `Host.schedule`.
- `service`: start/stop/restart services through `SystemChange(SERVICE)`.

## 7. Permissions and elevation

- Existing action kinds: `file_read`, `file_write`, `shell`, `network`.
- `python` and `process` reuse `shell` rules (target `python <script>`), so
  `shell.allow = ["python"]` works with no policy migration.
- Package installs additionally require a `network` action (`pypi.org`).
- New `ActionKind.ELEVATED`: always prompts, is never remembered, and is audited
  separately.
- Elevation flow: if `identity().elevated` is false, run the command through
  `Host.elevated_argv(argv)`, which triggers the OS prompt (UAC / sudo / pkexec).
  In a headless environment with no prompt available, fail with a clear message.

## 8. Cognition

- The system prompt gains:
  - an `Environment` section (compact machine summary), and
  - a `capabilities` section encoding the solve ladder: inspect the environment,
    prefer an existing tool then the OS command then Python stdlib then a
    package then a system change; write a script when logic exceeds a one-liner;
    verify every step; persist reusable scripts; try before asking; never write
    secrets.
- A planning/todo tool tracks goal decomposition and progress.

## 9. Storage layout

```
<config>/
  settings.toml
  credentials.toml
  workspace/<session-or-cwd-slug>/scripts/*.py
  jobs/<id>.json
  changes/changes.jsonl
  changes/<id>/snapshots/...
```

## 10. Configuration and CLI

New `Settings` fields: `env_probe`, `python_executable`, `python_timeout`,
`workspace_dir`, `auto_install`, `backup_dir`, `backup_retention_days`,
`elevation`.

New CLI: `--no-python`, `--workspace <dir>`, `--dry-run`, and the subcommands
`tm rollback`, `tm jobs`, `tm monitor`.

## 11. Testing and evals

- Per-platform `FakeHost` plus tolerant assertions on real probing.
- Change rollback round-trip tests.
- Multi-action permission tests.
- `python` tool tests: success, error, timeout, truncation, cwd, package
  pre-check, permission block.
- Evals: "find the largest directories by disk usage", "start a local HTTP
  service and verify it is reachable", "change a setting and roll it back".

## 12. Phased delivery and acceptance

- **Phase A - foundation** (done): Host abstraction, environment probe,
  `system_info`, `python` tool, capability ladder prompt. Accept: probing is
  correct on both platforms, the Python tool is reusable and verifiable,
  permissions apply.
- **Phase B - actuation lifecycle** (done): `process`, `service`, `package`,
  detached process management, dependency management.
- **Phase C - authority and safety** (done): guided elevation, `safety` change
  journal with file/package/service rollback, `/undo`, `--dry-run`, dangerous-op
  guard. Static `monitor` health checks remain for Phase D.
- **Phase D - perception**: `monitor`.
- **Phase E - autonomy**: OS-native scheduling, headless runs.
- **Phase F - remote**: SSH targets.
- **Phase G - reuse and specialization**: machine memory, solution library,
  script-to-tool/skill promotion, domain tool packs, optional GUI automation.

Dependencies: A is the prerequisite for everything. Service changes in B depend
on the snapshot capability in C, so B and C overlap.

## 13. Risks and open questions

- Legacy elevation without a desktop session cannot prompt UAC.
- Package rollback may be impossible when no older version is available.
- Distribution variance beyond systemd/apt-dnf.
- Secrets must never enter child environments, logs, or telemetry.
- Long-horizon correctness relies on verification hooks, not on the model's own
  claim of success.

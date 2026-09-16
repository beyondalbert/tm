from __future__ import annotations

import json
from pathlib import Path

from tm.ai.types import ToolCall
from tm.permissions.actions import Action, ActionKind, Decision
from tm.permissions.approval import ApprovalOutcome, AutoAllowApprover, AutoDenyApprover
from tm.permissions.audit import AuditLog
from tm.permissions.gate import PermissionChecker, action_for_tool, build_permission_hook
from tm.permissions.policy import Policy


def make_policy(tmp_path: Path, toml: str) -> Policy:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "policy.toml").write_text(toml)
    return Policy.load(cwd=tmp_path, config_dir=config_dir)


def test_default_ask() -> None:
    policy = Policy.from_dict({})
    decision, _ = policy.evaluate(Action(ActionKind.SHELL, "ls"))
    assert decision is Decision.ASK


def test_deny_wins_over_allow(tmp_path: Path) -> None:
    policy = make_policy(
        tmp_path,
        """
        [files.write]
        allow = ["**"]
        deny = ["**/.env"]
        """,
    )
    allowed, _ = policy.evaluate(Action(ActionKind.FILE_WRITE, "src/app.py", "write"))
    denied, _ = policy.evaluate(Action(ActionKind.FILE_WRITE, ".env", "write"))
    assert allowed is Decision.ALLOW
    assert denied is Decision.DENY


def test_shell_command_allow(tmp_path: Path) -> None:
    policy = make_policy(
        tmp_path,
        """
        default = "deny"
        [shell]
        allow = ["git *", "ls"]
        """,
    )
    assert policy.evaluate(Action(ActionKind.SHELL, "git status"))[0] is Decision.ALLOW
    assert policy.evaluate(Action(ActionKind.SHELL, "ls -la"))[0] is Decision.ALLOW
    assert policy.evaluate(Action(ActionKind.SHELL, "rm -rf /"))[0] is Decision.DENY


def test_network_host_rules(tmp_path: Path) -> None:
    policy = make_policy(
        tmp_path,
        """
        [network]
        allow = ["api.deepseek.com"]
        deny = ["evil.example.com"]
        """,
    )
    assert (
        policy.evaluate(Action(ActionKind.NETWORK, "api.deepseek.com"))[0]
        is Decision.ALLOW
    )
    assert (
        policy.evaluate(Action(ActionKind.NETWORK, "evil.example.com"))[0]
        is Decision.DENY
    )


def test_project_policy_extends_global(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "policy.toml").write_text('[shell]\nallow = ["git *"]\n')
    project = tmp_path / ".aiagent"
    project.mkdir()
    (project / "policy.toml").write_text('[shell]\nallow = ["pytest *"]\n')

    policy = Policy.load(cwd=tmp_path, config_dir=config_dir)
    assert policy.evaluate(Action(ActionKind.SHELL, "git status"))[0] is Decision.ALLOW
    assert policy.evaluate(Action(ActionKind.SHELL, "pytest -q"))[0] is Decision.ALLOW


def test_action_for_tool_mapping() -> None:
    read_action = action_for_tool("read", {"path": "a.txt"}, Path("."))
    edit_action = action_for_tool("edit", {"path": "a.txt"}, Path("."))
    shell_action = action_for_tool("shell", {"command": "ls"}, Path("."))
    assert read_action is not None and read_action.kind is ActionKind.FILE_READ
    assert edit_action is not None and edit_action.kind is ActionKind.FILE_WRITE
    assert shell_action is not None and shell_action.kind is ActionKind.SHELL
    assert action_for_tool("unknown", {}, Path(".")) is None


async def test_checker_allow_and_deny(tmp_path: Path) -> None:
    policy = Policy.from_dict({"shell": {"allow": ["ls *"], "deny": ["rm *"]}})
    allow_checker = PermissionChecker(policy, approver=AutoDenyApprover())
    deny_checker = PermissionChecker(policy, approver=AutoAllowApprover())

    assert await allow_checker.authorize(Action(ActionKind.SHELL, "ls -la")) is True
    assert await deny_checker.authorize(Action(ActionKind.SHELL, "rm -rf /")) is False


async def test_checker_asks_and_remembers(tmp_path: Path) -> None:
    policy = Policy.from_dict({"default": "ask"})

    class AlwaysRemember:
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, action, reason) -> ApprovalOutcome:
            self.calls += 1
            return ApprovalOutcome(allowed=True, remember=True)

    approver = AlwaysRemember()
    checker = PermissionChecker(policy, approver=approver)
    assert await checker.authorize(Action(ActionKind.SHELL, "echo hi")) is True
    assert await checker.authorize(Action(ActionKind.SHELL, "echo hi")) is True
    assert approver.calls == 1


async def test_audit_log_writes_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.jsonl"
    checker = PermissionChecker(
        Policy.from_dict({"default": "allow"}),
        approver=AutoDenyApprover(),
        audit=AuditLog(log_path),
    )
    await checker.authorize(Action(ActionKind.FILE_READ, "a.txt", "read"))

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "file_read"
    assert entry["allowed"] is True


async def test_permission_hook_blocks_denied_tool(tmp_path: Path) -> None:
    policy = Policy.from_dict({"files": {"write": {"deny": ["**"]}}})
    checker = PermissionChecker(policy, approver=AutoDenyApprover())
    hook = build_permission_hook(checker, tmp_path)

    call = ToolCall(id="1", name="write", arguments={"path": "x.txt", "content": "y"})
    decision = await hook(call, None)
    assert decision is not None
    assert decision.block is True
    assert "Permission denied" in (decision.reason or "")


async def test_permission_hook_allows(tmp_path: Path) -> None:
    policy = Policy.from_dict({"files": {"read": {"allow": ["**"]}}})
    checker = PermissionChecker(policy, approver=AutoDenyApprover())
    hook = build_permission_hook(checker, tmp_path)

    call = ToolCall(id="1", name="read", arguments={"path": "x.txt"})
    assert await hook(call, None) is None

from __future__ import annotations

import json
from pathlib import Path

from tm.ai.types import ToolCall
from tm.permissions.actions import Action, ActionKind, Decision
from tm.permissions.approval import ApprovalOutcome, AutoAllowApprover, AutoDenyApprover
from tm.permissions.audit import AuditLog
from tm.permissions.gate import (
    PermissionChecker,
    action_for_tool,
    actions_for_tool,
    build_permission_hook,
)
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


def test_folder_rule_covers_subtree(tmp_path: Path) -> None:
    policy = Policy.from_dict({"files": {"write": {"allow": ["src"]}}}, cwd=tmp_path)
    assert policy.evaluate(Action(ActionKind.FILE_WRITE, "src/a/b.txt", "write"))[0] is Decision.ALLOW
    assert policy.evaluate(Action(ActionKind.FILE_WRITE, "src", "write"))[0] is Decision.ALLOW
    assert policy.evaluate(Action(ActionKind.FILE_WRITE, "other/b.txt", "write"))[0] is Decision.ASK


def test_folder_rule_with_glob_forms(tmp_path: Path) -> None:
    for pattern in ("src", "src/", "src/**"):
        policy = Policy.from_dict({"files": {"read": {"allow": [pattern]}}}, cwd=tmp_path)
        assert policy.evaluate(Action(ActionKind.FILE_READ, "src/a/b.txt", "read"))[0] is Decision.ALLOW


def test_folder_deny_covers_subtree(tmp_path: Path) -> None:
    policy = Policy.from_dict(
        {"default": "allow", "files": {"read": {"deny": ["secrets"]}}}, cwd=tmp_path
    )
    assert policy.evaluate(Action(ActionKind.FILE_READ, "secrets/token.txt", "read"))[0] is Decision.DENY
    assert policy.evaluate(Action(ActionKind.FILE_READ, "src/app.py", "read"))[0] is Decision.ALLOW


async def test_always_allows_remember_the_folder(tmp_path: Path) -> None:
    policy = Policy.from_dict({"default": "ask"}, cwd=tmp_path)

    class RememberOnce:
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, action, reason) -> ApprovalOutcome:
            self.calls += 1
            return ApprovalOutcome(allowed=True, remember=True)

    approver = RememberOnce()
    checker = PermissionChecker(policy, approver=approver)

    assert await checker.authorize(Action(ActionKind.FILE_READ, "src/a/f1.txt")) is True
    # same folder: remembered, no second prompt
    assert await checker.authorize(Action(ActionKind.FILE_READ, "src/a/f2.txt")) is True
    assert approver.calls == 1
    # different folder: prompt again
    assert await checker.authorize(Action(ActionKind.FILE_READ, "other/f.txt")) is True
    assert approver.calls == 2


async def test_always_command_remembers_exact_command(tmp_path: Path) -> None:
    policy = Policy.from_dict({"default": "ask"}, cwd=tmp_path)

    class RememberOnce:
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, action, reason) -> ApprovalOutcome:
            self.calls += 1
            return ApprovalOutcome(allowed=True, remember=True)

    approver = RememberOnce()
    checker = PermissionChecker(policy, approver=approver)

    assert await checker.authorize(Action(ActionKind.SHELL, "git status")) is True
    assert await checker.authorize(Action(ActionKind.SHELL, "git status")) is True
    assert approver.calls == 1
    assert await checker.authorize(Action(ActionKind.SHELL, "git push")) is True
    assert approver.calls == 2


def test_python_tool_reuses_shell_action() -> None:
    action = action_for_tool("python", {"description": "parse logs"}, Path("."))
    assert action is not None
    assert action.kind is ActionKind.SHELL
    assert "python" in action.target


def test_python_packages_also_require_network() -> None:
    actions = actions_for_tool(
        "python", {"description": "call an API", "packages": ["requests"]}, Path(".")
    )
    kinds = {action.kind for action in actions}
    assert kinds == {ActionKind.SHELL, ActionKind.NETWORK}


async def test_python_hook_blocks_when_network_denied(tmp_path: Path) -> None:
    policy = Policy.from_dict(
        {"shell": {"allow": ["python *"]}, "network": {"deny": ["pypi.org"]}}
    )
    checker = PermissionChecker(policy, approver=AutoDenyApprover())
    hook = build_permission_hook(checker, tmp_path)

    call = ToolCall(
        id="1", name="python", arguments={"description": "x", "packages": ["requests"]}
    )
    decision = await hook(call, None)
    assert decision is not None
    assert decision.block is True
    assert "network" in (decision.reason or "")


async def test_python_hook_allows_plain_script(tmp_path: Path) -> None:
    policy = Policy.from_dict({"shell": {"allow": ["python *"]}})
    checker = PermissionChecker(policy, approver=AutoDenyApprover())
    hook = build_permission_hook(checker, tmp_path)

    call = ToolCall(id="1", name="python", arguments={"description": "x"})
    assert await hook(call, None) is None


def test_elevate_maps_to_elevated_action() -> None:
    actions = actions_for_tool("elevate", {"command": "systemctl restart nginx"}, Path("."))
    assert [action.kind for action in actions] == [ActionKind.ELEVATED]


async def test_elevation_is_always_asked_and_never_remembered() -> None:
    class Counting:
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, action, reason) -> ApprovalOutcome:
            self.calls += 1
            return ApprovalOutcome(allowed=True, remember=True)

    approver = Counting()
    checker = PermissionChecker(Policy.from_dict({"default": "allow"}), approver=approver)
    action = Action(ActionKind.ELEVATED, "systemctl restart nginx")
    assert await checker.authorize(action) is True
    assert await checker.authorize(action) is True
    assert approver.calls == 2


async def test_dangerous_command_forces_approval() -> None:
    policy = Policy.from_dict({"default": "allow", "shell": {"allow": ["shutdown *"]}})
    checker = PermissionChecker(policy, approver=AutoDenyApprover())
    assert await checker.authorize(Action(ActionKind.SHELL, "shutdown now")) is False


def test_process_service_package_map_to_shell() -> None:
    cases = [
        ("process", {"action": "start", "command": "serve"}),
        ("service", {"action": "start", "name": "nginx"}),
        ("package", {"action": "install", "name": "jq"}),
    ]
    for name, args in cases:
        actions = actions_for_tool(name, args, Path("."))
        assert len(actions) == 1
        assert actions[0].kind is ActionKind.SHELL
